from setuptools import find_packages, setup

package_name = "intent_node"

setup(
    name=package_name,
    version="0.2.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Your Name",
    maintainer_email="you@example.com",
    description="Gesture UDP bridge: gestures.py -> /gesture/intent",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "intent_node = intent_node.intent_node:main",
        ],
    },
)
