from setuptools import find_packages, setup

package_name = "controller_node"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Bruno",
    maintainer_email="bmmc136@gmail.com",
    description="Cascade controller: velocity target -> /control/attitude_setpoint",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "controller_node = controller_node.controller_node:main",
        ],
    },
)
