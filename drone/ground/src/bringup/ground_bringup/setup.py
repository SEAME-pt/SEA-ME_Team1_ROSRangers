from glob import glob

from setuptools import setup

package_name = "ground_bringup"

# No Python module: only the launch and parameter files are installed
setup(
    name=package_name,
    version="0.1.0",
    packages=[],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Bruno",
    maintainer_email="bmmc136@gmail.com",
    description="The ground's modes (demo, real, sim): launch and parameter files",
    license="MIT",
    tests_require=["pytest"],
)
