
import setuptools
import codecs
import os.path

import ribosome


here = os.path.abspath(os.path.dirname(__file__))

with codecs.open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()


setuptools.setup(
    name='ribosome.tool',
    version=ribosome.__version__,
    description='Yet another project deploy and release tool',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/alexandervpetrov/ribosome',
    author='Oleksandr Petrov',
    author_email='alexandervpetrov@gmail.com',
    license='MIT',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
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
        'setuptools-scm',
        'boto3',
        'fabric3',
    ],
    entry_points={
        'console_scripts': [
            'ribosome=ribosome:cli',
        ],
    },
)
