from setuptools import find_packages, setup

package_name = "giga_serial_bridge"

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
    maintainer="robot",
    maintainer_email="robot@local",
    description="USB serial HOLD/RUN bridge to Giga movement.ino",
    license="Apache-2.0",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "giga_serial_node = giga_serial_bridge.giga_serial_node:main",
        ],
    },
)
