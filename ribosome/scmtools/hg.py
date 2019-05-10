
import logging
import pathlib

from . import utils
from . import errors

log = logging.getLogger('scmtools.hg')


def detect(rootpath):
    rootpath = pathlib.Path(rootpath)
    scmdir = rootpath.joinpath('.hg')
    return scmdir.exists() and scmdir.is_dir()


def ensure_hg_command_available(rootpath):
    try:
        utils.run('hg help'.split(), cwd=rootpath)
    except errors.CommandRunError as e:
        raise errors.ScmError('Command [hg] is not available') from e


def describe(rootpath):
    ensure_hg_command_available(rootpath)

    description = identify_repo(rootpath)
    branch, revision, tags, dirty = description

    distance = 0
    if tags:
        # clean tag found
        version_tag = tags[0]
    elif revision == '0' * 12:
        # initial or no repository state
        version_tag = None
    else:
        # general case
        version_tag = find_latest_version_tag(rootpath)
        distance = graph_distance(rootpath, version_tag)
        if version_tag is not None:
            # not counting separate Mercurial commit for tag
            distance -= 1

    scminfo = dict(
        scm='hg',
        revision=revision,
        branch=branch,
        tag=version_tag,
        distance=distance,
        dirty=dirty,
    )
    return scminfo


def archive(rootpath, targetdir):
    ensure_hg_command_available(rootpath)
    log.debug('Making archive of Mercurial repo [%s] to [%s]: starting...', rootpath, targetdir)
    utils.run(
        ['hg', 'archive', targetdir],
        cwd=rootpath,
        errormsg='Failed to make repository archive',
    )
    log.info('Making archive of Mercurial repo [%s] to [%s]: done', rootpath, targetdir)


def identify_repo(rootpath):
    output = utils.run(
        'hg id --id --branch --tags'.split(),
        cwd=rootpath,
        errormsg='Failed to identify repository info',
    )
    revision, branch, *tags = output.split()
    # TODO: enable tag prefixes (similar to parsing inside setuptools_scm)

    # not interested in 'tip'
    tags = [tag for tag in tags if tag != 'tip']

    dirty = False
    if revision[-1] == '+':
        dirty = True
        revision = revision[:-1]

    return (branch, revision, tags, dirty)


def find_latest_version_tag(rootpath):
    """Gets all tags containing a '.' from oldest to newest"""
    output = utils.run(
        # ["hg", "log", "-r", r"ancestors(.) and tag('re:\.')", "--template", r"{tags}\n"],  # only tags with dots inside allowed
        ["hg", "log", "-r", r"ancestors(.) and tag('re:')", "--template", r"{tags}\n"],  # any tags allowed
        cwd=rootpath,
        errormsg='Failed to find latest tag',
    )
    if not output:
        return None
    tags = output.split()
    if tags[-1] == 'tip':
        tags = tags[:-1]
    if not tags:
        return None
    latest_tag = tags[-1].split()[-1]
    return latest_tag


def graph_distance(rootpath, rev1, rev2="."):
    if rev1 is None:
        rev1 = 'null'
    if rev2 is None:
        rev2 = 'null'
    output = utils.run(
        ["hg", "log", "-q", "-r", "{}::{}".format(rev1, rev2)],
        cwd=rootpath,
        errormsg='Failed to find graph distance',
    )
    distance = len(output.splitlines()) - 1
    return distance


def changes_inside_branch_since_tag(rootpath, tag):
    assert tag
    revset = (
        r"(branch(.)"  # look for revisions in this branch only
        r" and tag({tag!r})::."  # after the last tag
        # ignore commits that only modify .hgtags and nothing else:
        r" and (merge() or file('re:^(?!\.hgtags).*$'))"
        r" and not tag({tag!r}))"  # ignore the tagged commit itself
    ).format(tag=tag)
    output = utils.run(
        ["hg", "log", "-r", revset, "--template", r"{node|short}\n"],
        cwd=rootpath,
        errormsg='Failed to detect changes since tag',
    )
    number_of_commits = len(output.splitlines())
    return number_of_commits
