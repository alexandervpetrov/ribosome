# Ribosome

The purpose of this tool is to improve release culture for small projects,
to provide useful **conventions** and suitable **vocabulary** for speaking about
software pieces, and to simplify making and deploying simple services.

Ribosome imposes some reasonable concepts and assumptions that, I believe,
can clarify release process and be helpful for a wide range of projects.

Let's begin with the description of what I consider proper release process.
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

## Typical workflow

For a start, in the root of your project's folder, the file `codons.yaml`
should be placed - this is where various settings for Ribosome live.
You can generate an empty one by the command: `ribosome init`.

    $ ribosome init

Ribosome releases project versions, i.e., named tags of source code repository.
Tag expected to be in one of the forms: `N.N.N`, `N.N.NaN`, `N.N.NbN` and `devxxxx` -
where `N` is for a number and `xxxx` for any alphanumerical suffix.

At any time you can find out what version your project folder is: `ribosome version info`.

    $ ribosome version info

After you tagged repository with a version, you can make a release: `ribosome release`.

    $ ribosome release
