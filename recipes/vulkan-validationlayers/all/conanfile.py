from conan import ConanFile, conan_version
from conan.errors import ConanException, ConanInvalidConfiguration
from conan.tools.apple import fix_apple_shared_install_name
from conan.tools.build import check_min_cppstd
from conan.tools.cmake import CMake, CMakeDeps, CMakeToolchain, cmake_layout
from conan.tools.env import VirtualBuildEnv
from conan.tools.files import apply_conandata_patches, copy, export_conandata_patches, get, mkdir, rename, replace_in_file, rm, save
from conan.tools.gnu import PkgConfigDeps
from conan.tools.scm import Version
import functools
import glob
import os
import shutil
import yaml

required_conan_version = ">=1.55.0"


class VulkanValidationLayersConan(ConanFile):
    name = "vulkan-validationlayers"
    description = "Khronos official Vulkan validation layers for Windows, Linux, Android, and MacOS."
    license = "Apache-2.0"
    topics = ("vulkan", "validation-layers")
    homepage = "https://github.com/KhronosGroup/Vulkan-ValidationLayers"
    url = "https://github.com/conan-io/conan-center-index"
    package_type = "static-library"
    settings = "os", "arch", "compiler", "build_type"
    options = {
        "fPIC": [True, False],
        "with_wsi_xcb": [True, False],
        "with_wsi_xlib": [True, False],
        "with_wsi_wayland": [True, False],
    }
    default_options = {
        "fPIC": True,
        "with_wsi_xcb": True,
        "with_wsi_xlib": True,
        "with_wsi_wayland": True,
    }

    short_paths = True

    @property
    def _dependencies_filename(self):
        return f"dependencies-{self.version}.yml"

    @property
    @functools.lru_cache(1)
    def _dependencies_versions(self):
        dependencies_filepath = os.path.join(self.recipe_folder, "dependencies", self._dependencies_filename)
        if not os.path.isfile(dependencies_filepath):
            raise ConanException(f"Cannot find {dependencies_filepath}")
        cached_dependencies = yaml.safe_load(open(dependencies_filepath))
        return cached_dependencies

    @property
    def _needs_wayland_for_build(self):
        return (self.options.get_safe("with_wsi_wayland") and
                (Version(self.version) < "1.3.231" or Version(self.version) >= "1.3.243.0"))

    @property
    def _needs_pkg_config(self):
        return self.options.get_safe("with_wsi_xcb") or \
               self.options.get_safe("with_wsi_xlib") or \
               self._needs_wayland_for_build

    @property
    def _min_cppstd(self):
        if Version(self.version) >= "1.3.235":
            return "17"
        return "11"

    @property
    def _compilers_minimum_version(self):
        return {
            "17": {
                "apple-clang": "9",
                "clang": "6",
                "gcc": "7",
                "msvc": "191",
                "Visual Studio": "15.7",
            },
        }.get(self._min_cppstd, {})

    def export(self):
        copy(self, f"dependencies/{self._dependencies_filename}", self.recipe_folder, self.export_folder)

    def export_sources(self):
        export_conandata_patches(self)

    def config_options(self):
        if self.settings.os not in ["Linux", "FreeBSD"]:
            del self.options.with_wsi_xcb
            del self.options.with_wsi_xlib
            del self.options.with_wsi_wayland
        if self.settings.os == "Windows":
            del self.options.fPIC

    def layout(self):
        cmake_layout(self, src_folder="src")

    def requirements(self):
        self.requires("robin-hood-hashing/3.11.5")
        self.requires(self._require("spirv-headers"))
        if Version(conan_version).major < "2":
            # TODO: set private=True, once the issue is resolved https://github.com/conan-io/conan/issues/9390
            self.requires(self._require("spirv-tools"), private=not hasattr(self, "settings_build"))
        else:
            self.requires(self._require("spirv-tools"))
        self.requires(self._require("vulkan-headers"), transitive_headers=True)
        if self.options.get_safe("with_wsi_xcb") or self.options.get_safe("with_wsi_xlib"):
            self.requires("xorg/system")
        if self._needs_wayland_for_build:
            self.requires("wayland/1.22.0")

    def _require(self, recipe_name):
        if recipe_name not in self._dependencies_versions:
            raise ConanException(f"{recipe_name} is missing in {self._dependencies_filename}")
        return f"{recipe_name}/{self._dependencies_versions[recipe_name]}"

    def validate(self):
        if self.settings.compiler.get_safe("cppstd"):
            check_min_cppstd(self, self._min_cppstd)

        def loose_lt_semver(v1, v2):
            lv1 = [int(v) for v in v1.split(".")]
            lv2 = [int(v) for v in v2.split(".")]
            min_length = min(len(lv1), len(lv2))
            return lv1[:min_length] < lv2[:min_length]

        minimum_version = self._compilers_minimum_version.get(str(self.settings.compiler), False)
        if minimum_version and loose_lt_semver(str(self.settings.compiler.version), minimum_version):
            raise ConanInvalidConfiguration(
                f"{self.ref} requires C++{self._min_cppstd}, which your compiler does not support.",
            )

        if self.dependencies["spirv-tools"].options.shared:
            raise ConanInvalidConfiguration("vulkan-validationlayers can't depend on shared spirv-tools")

        if self.settings.compiler == "gcc" and Version(self.settings.compiler.version) < "5":
            raise ConanInvalidConfiguration("gcc < 5 is not supported")

    def build_requirements(self):
        if self._needs_pkg_config and not self.conf.get("tools.gnu:pkg_config", check_type=str):
            self.tool_requires("pkgconf/2.1.0")
        if Version(self.version) >= "1.3.239":
            self.tool_requires("cmake/[>=3.17.2 <4]")

    def source(self):
        get(self, **self.conan_data["sources"][self.version], strip_root=True)

    def generate(self):
        env = VirtualBuildEnv(self)
        env.generate()

        tc = CMakeToolchain(self)
        if Version(self.version) >= "1.3.239":
            tc.cache_variables["VVL_CLANG_TIDY"] = False
        if Version(self.version) < "1.3.234":
            tc.variables["VULKAN_HEADERS_INSTALL_DIR"] = self.dependencies["vulkan-headers"].package_folder.replace("\\", "/")
        tc.variables["USE_CCACHE"] = False
        if self.settings.os in ["Linux", "FreeBSD"]:
            tc.variables["BUILD_WSI_XCB_SUPPORT"] = self.options.get_safe("with_wsi_xcb")
            tc.variables["BUILD_WSI_XLIB_SUPPORT"] = self.options.get_safe("with_wsi_xlib")
            tc.variables["BUILD_WSI_WAYLAND_SUPPORT"] = self.options.get_safe("with_wsi_wayland")
        elif self.settings.os == "Android":
            tc.variables["BUILD_WSI_XCB_SUPPORT"] = False
            tc.variables["BUILD_WSI_XLIB_SUPPORT"] = False
            tc.variables["BUILD_WSI_WAYLAND_SUPPORT"] = False
        tc.variables["BUILD_WERROR"] = False
        tc.variables["BUILD_TESTS"] = False
        tc.variables["INSTALL_TESTS"] = False
        tc.variables["BUILD_LAYERS"] = True
        tc.variables["BUILD_LAYER_SUPPORT_FILES"] = True
        tc.cache_variables["SPIRV-Tools-opt_DIR"] = self.generators_folder.replace("\\", "/")
        tc.generate()

        deps = CMakeDeps(self)
        deps.generate()

        save(self, os.path.join(self.generators_folder, "SPIRV-Tools-optConfig.cmake"),
             """include(CMakeFindDependencyMacro)
             find_dependency(SPIRV-Tools)""")

        if self._needs_pkg_config:
            deps = PkgConfigDeps(self)
            deps.generate()

    def _patch_sources(self):
        apply_conandata_patches(self)
        # Vulkan-ValidationLayers relies on Vulkan-Headers version from CMake config file
        # to set api_version in its manifest file, but this value MUST have format x.y.z (no extra number).
        # FIXME: find a way to force correct version in CMakeDeps of vulkan-headers recipe?
        # NOTE: At version 1.3.239, the JSON_API_VERSION was removed from the cmakelists file, 
        if Version(self.version) >= "1.3.235" and Version(self.version) < "1.3.239":
            vk_version = Version(self.dependencies["vulkan-headers"].ref.version)
            sanitized_vk_version = f"{vk_version.major}.{vk_version.minor}.{vk_version.patch}"
            replace_in_file(
                self, os.path.join(self.source_folder, "layers", "CMakeLists.txt"),
                "set(JSON_API_VERSION ${VulkanHeaders_VERSION})",
                f"set(JSON_API_VERSION \"{sanitized_vk_version}\")",
            )
        if self.settings.os == "Android":
            # INFO: libVkLayer_utils.a: error: undefined symbol: __android_log_print
            # https://github.com/KhronosGroup/Vulkan-ValidationLayers/commit/a26638ae9fdd8c40b56d4c7b72859a5b9a0952c9
            replace_in_file(self, os.path.join(self.source_folder, "CMakeLists.txt"),
                        "VkLayer_utils PUBLIC Vulkan::Headers", "VkLayer_utils PUBLIC Vulkan::Headers -landroid -llog")
        if not self.options.get_safe("fPIC"):
            replace_in_file(self, os.path.join(self.source_folder, "CMakeLists.txt"),
                        "CMAKE_POSITION_INDEPENDENT_CODE ON", "CMAKE_POSITION_INDEPENDENT_CODE OFF")

    def build(self):
        self._patch_sources()
        cmake = CMake(self)
        cmake.configure()
        cmake.build()

    def package(self):
        copy(self, "LICENSE.txt", src=self.source_folder, dst=os.path.join(self.package_folder, "licenses"))
        cmake = CMake(self)
        cmake.install()
        rm(self, "*.pdb", os.path.join(self.package_folder, "bin"))
        if self.settings.os == "Windows":
            # import lib is useless, validation layers are loaded at runtime
            lib_dir = os.path.join(self.package_folder, "lib")
            rm(self, "VkLayer_khronos_validation.lib", lib_dir)
            rm(self, "libVkLayer_khronos_validation.dll.a", lib_dir)
            # move dll and json manifest files in bin folder
            bin_dir = os.path.join(self.package_folder, "bin")
            mkdir(self, bin_dir)
            for ext in ("*.dll", "*.json"):
                for bin_file in glob.glob(os.path.join(lib_dir, ext)):
                    shutil.move(bin_file, os.path.join(bin_dir, os.path.basename(bin_file)))
        else:
            # Move json files to res, but keep in mind to preserve relative
            # path between module library and manifest json file
            rename(self, os.path.join(self.package_folder, "share"), os.path.join(self.package_folder, "res"))
        fix_apple_shared_install_name(self)

    def package_info(self):
        self.cpp_info.libs = ["VkLayer_utils"]

        manifest_subfolder = "bin" if self.settings.os == "Windows" else os.path.join("res", "vulkan", "explicit_layer.d")
        vk_layer_path = os.path.join(self.package_folder, manifest_subfolder)
        self.runenv_info.prepend_path("VK_LAYER_PATH", vk_layer_path)

        # Update runtime discovery paths to allow libVkLayer_khronos_validation.{so,dll,dylib} to be discovered
        # and loaded by vulkan-loader when the consumer executes
        # This is necessary because this package exports a static lib to link against and a dynamic lib to load at runtime
        runtime_lib_discovery_path = "LD_LIBRARY_PATH"
        if self.settings.os == "Windows":
            runtime_lib_discovery_path = "PATH"
        if self.settings.os == "Macos":
            runtime_lib_discovery_path = "DYLD_LIBRARY_PATH"
        for libdir in [os.path.join(self.package_folder, libdir) for libdir in self.cpp_info.libdirs]:
            self.runenv_info.prepend_path(runtime_lib_discovery_path, libdir)

        # TODO: to remove after conan v2, it allows to not break consumers still relying on virtualenv generator
        self.env_info.VK_LAYER_PATH.append(vk_layer_path)

        if self.settings.os == "Android":
            self.cpp_info.system_libs.extend(["android", "log"])
