#!/usr/bin/env python

import os
import io
import re
import setuptools


HERE = os.path.abspath(os.path.dirname(__file__))


def get_readme():
    with io.open(os.path.join(HERE, 'README.md'), 'rt', encoding='utf8') as f:
        readme = f.read()
        return readme


def get_version_string():
    with io.open(os.path.join(HERE, 'ribosome', '__init__.py'), 'rt', encoding='utf8') as f:
        version = re.search(r'__version__ = \'(.*?)\'', f.read()).group(1)
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
    ],
    entry_points={
        'console_scripts': [
            'ribosome=ribosome:cli',
        ],
    },
)
