from setuptools import setup, find_packages
setup(
    name='edc-client',
    version='0.1',
    packages=find_packages(),
    entry_points={
        'console_scripts': [
            'edc-client=edc_client.main:main',
        ],
    },
)
