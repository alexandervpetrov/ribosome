# Ribosome

The purpose of this tool is to improve release culture for small projects,
to provide useful **conventions** and suitable **vocabulary** for speaking about
software pieces, and to simplify making and deploying simple services.

Ribosome imposes some reasonable concepts and assumptions that, I believe,
can clarify the release process and be helpful for a wide range of projects.

Let's begin with the description of what I consider the proper release process.
You have a project - some version controlled thing, a source code repository
for example. At some point in time, you decide to fix it state by tagging
with version. Then you build it, run tests against it, and archive all needed files
as the release (release archive). You upload the archive to some persistent
storage for further use. The next step is to deploy the release to some working
place and run by loading one or more services with appropriate configurations
attached. Notice, it is *essential* to support the clear separation between release,
deploy and run stages. Also, valuable properties are reproducibility and
idempotence of steps. In a nutshell, exactly process described can be achieved
with the help of Ribosome and a bit of discipline.


## Installation

Ribosome is available on PyPI:
[https://pypi.org/project/ribosome.tool/](https://pypi.org/project/ribosome.tool/)

To install use [pipenv](http://pipenv.org) (or pip, of course):

    $ pipenv install ribosome.tool


## Typical workflow

For a start, in the root of your project's folder, the file `codons.yaml`
should be placed - this is where various settings for Ribosome live.
You can generate an empty one by the command:

    $ ribosome init

Suppose you have already configured your project for Ribosome via `codons.yaml` -
I will describe later how to do this.

Ribosome releases project versions, i.e., named tags of source code repository.
Tag expected to be in one of the forms: `N.N.N`, `N.N.NaN`, `N.N.NbN` and `devxxxx` -
where `N` is for a number and `xxxx` for any alphanumerical suffix.

At any time you can find out what version your project folder is:

    $ ribosome version info

After you tagged repository with a version, you can make a release:

    $ ribosome release

During this process release archive is made and uploaded to Amazon S3 bucket.
Credentials for S3 access expected to be
[already configured](https://boto3.readthedocs.io/en/latest/guide/quickstart.html#configuration)
in the environment.

Any uploaded to S3 release can be deployed to remote host accessible via SSH:

    $ ribosome deploy <version> <host>

At remote host `~/releases` folder is used for archives upload and
`~/projects` is used for release deployment and management.
During deploy process runtime environment will be set up.
After that release files and runtime are considered immutable.

This and any other Ribosome operations using SSH (the ones that taking `host` parameter)
require that you configure host settings and alias inside `~/.ssh/config`.

For deployed release you can load and unload service(s):

    $ ribosome load <version> <service> <config> <host>

In case of a simple upgrade, when all services of interest at host are already running,
you can quickly reload them all of them (jump) to the new version:

    $ ribosome jump <version> <host>

Load, unload and jump commands require entering sudo password for the remote host.

When (un)loading services Ribosome save their status in files
`~/projects/<project>/services.index.yaml` so you can easily view
the state of the remote host for current project via command:

    $ ribosome show <host>

Alternatively, adding an option `-a` will scan for services of all projects
deployed at host:

    $ ribosome show <host> -a


## Project configuration

*TODO: Create examples for web projects (aiohttp, Django, Nginx, ...).*
