#!/usr/bin/env python

import os
import io
import re
import setuptools


THIS_FILE_DIR = os.path.abspath(os.path.dirname(__file__))


def get_readme():
    readme_path = os.path.join(THIS_FILE_DIR, 'README.md')
    with io.open(readme_path, 'rt', encoding='utf8') as istream:
        readme = istream.read()
    return readme


def get_version_string():
    module_with_version_def_path = os.path.join(THIS_FILE_DIR, 'ribosome', '__init__.py')
    with io.open(module_with_version_def_path, 'rt', encoding='utf8') as istream:
        module_source_code = istream.read()
    version = re.search(r'__version__ = \'(.*?)\'', module_source_code).group(1)
    return version


setuptools.setup(
    name='ribosome.tool',
    version=get_version_string(),
    description='Yet another project deploy and release tool',
    long_description=get_readme(),
    long_description_content_type='text/markdown',
    url='https://github.com/alexandervpetrov/ribosome',
    author='Sashko Petrov',
    author_email='alexandervpetrov@gmail.com',
    license='MIT',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Natural Language :: English',
        'License :: OSI Approved :: MIT License',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.6',
        'Topic :: Software Development :: Build Tools',
        'Topic :: System :: Installation/Setup',
        'Topic :: System :: Software Distribution',
    ],
    keywords='build deploy release',
    packages=setuptools.find_packages(exclude=['contrib', 'docs', 'tests*']),
    python_requires='>=3.6',
    install_requires=[
        'click',
        'ruamel.yaml',
        'coloredlogs',
        'boto3',
        'fabric3',
        'requests',
    ],
    entry_points={
        'console_scripts': [
            'ribosome=ribosome:cli',
        ],
    },
)
