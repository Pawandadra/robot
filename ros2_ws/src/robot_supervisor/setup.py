from setuptools import find_packages, setup

package_name = "robot_supervisor"

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
    description="Face presence + interaction -> Giga HOLD/RUN",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "supervisor_node = robot_supervisor.supervisor_node:main",
        ],
    },
)
