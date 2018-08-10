# Ribosome

The purpose of this tool is to improve release culture for small projects,
to provide useful **conventions** and suitable **vocabulary** for speaking about
software pieces, and to simplify making and deploying simple services.

Ribosome imposes some reasonable concepts and assumptions that, I believe,
can clarify the release process and be helpful for a wide range of projects.

Let's begin with the description of what I consider the proper release process.
You have a project - some version controlled thing, a source code repository
for example. At some point in time, you decide to freeze its state by tagging
with version. Then you build it, run tests against it, and archive all needed files
as the release (release archive). You upload the archive to some persistent
storage for further use. The next step is to deploy the release to some working
place and run by loading one or more services with appropriate configurations
attached. Notice, it is *essential* to support the clear separation between release,
deploy and run stages. Also, valuable properties are reproducibility and
idempotence of steps. In a nutshell, exactly process described can be achieved
with the help of Ribosome and a bit of discipline.


## Getting started

Ribosome is available on PyPI:
[https://pypi.org/project/ribosome.tool/](https://pypi.org/project/ribosome.tool/)

To install use [pipenv](http://pipenv.org) (or pip, of course):

    $ pipenv install ribosome.tool

Read help:

    $ ribosome --help

    Usage: ribosome [OPTIONS] COMMAND [ARGS]...

      Yet another project deploy and release tool

    Options:
      -v, --verbose  More detailed logging to console
      -f, --force    Rewrite existing files
      --version      Show the version and exit.
      --help         Show this message and exit.

    Commands:
      deploy   Deploy release artifacts to host
      do       Run command for service at remote host
      gc       Uninstall deployed versions at remote host
      init     Initialize Ribosome project
      jump     Reload all services to version at remote host
      load     Load service at remote host
      ls       List deployed versions at remote host
      release  Make release and publish artifacts
      show     Show loaded services at remote host
      unload   Unload service at remote host
      version  Show version or update meta descriptor

And help for specific command:

    $ ribosome <command> --help


## Typical workflow

For a start, in the root of your project's folder, the file `codons.yaml`
should be placed - this is where various settings for Ribosome live.
You can generate an empty one by the command:

    $ ribosome init

Suppose you have already configured your project for Ribosome via `codons.yaml` -
I will describe later how to do this.

Ribosome releases project versions, i.e., named tags of source code repository.
Tag expected to be in one of the forms: `N.N.N`, `N.N.NaN`, `N.N.NbN` and `dev.XXXX` -
where `N` is for a number and `XXXX` for any alphanumerical suffix with dots inside allowed.

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

This and any other Ribosome operations using SSH (the ones taking `host` parameter)
require you to configure host settings and alias inside `~/.ssh/config`.

For deployed release you can load and unload service(s):

    $ ribosome load <version> <service> <config> <host>

In case of a simple upgrade, when all services of interest are already running at host,
you can quickly reload all of them (jump) to the new version:

    $ ribosome jump <version> <host>

Load, unload and jump commands require entering sudo password for the remote host.

When (un)loading services Ribosome save their status in files
`~/projects/<project>/services.index.yaml` so you can easily view
the state of the remote host for current project via command:

    $ ribosome show <host>

Alternatively, adding an option `-a` will scan for services of all projects
deployed at host:

    $ ribosome show <host> -a

At any time you can list deployed versions for current project with:

    $ ribosome ls <host>

or get a list for all projects with `-a` option.

Deployed versions can be removed from remote host with:

    $ ribosome gc <versions> <host>

where first argument is versions pattern - symbols `*` and `?` are recognized.
Versions with services currently loaded cannot be removed by this command,
you should unload all services from version to be able to remove it.


## Project configuration

Technically, Ribosome is just simple commands and remote commands runner
with few features for SCM management.
It does not do anything special or complex in terms of functionality.
What it does do - is the separation of concerns and operations specifications
to enforce and support the release process with reproducibility and immutability in mind.

Ribosome on its own does not know how to *release* projects or *load* services.
It provides only the **structure** or **interface** for these operations.

So, to configure a project for Ribosome via the file `codons.yaml` -
you need to provide build, setup and other commands - your project's release process
implementation of Ribosome interface.

Please, see examples for
[Nginx configuration files](https://github.com/alexandervpetrov/ribosome-example-nginx)
and
[Django project](https://github.com/alexandervpetrov/ribosome-example-django).


## Acknowledgments

This project is created with the support of [Orderry](https://orderry.com/) company,
my thanks for the opportunities and understanding.

For the idea of the name `Ribosome` my gratitude goes to
[Ievgeniia Prekrasna](https://www.facebook.com/preckrasna).
