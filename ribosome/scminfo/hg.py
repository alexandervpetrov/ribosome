
import pathlib

from ribosome.scminfo import utils


def detect(rootpath):
    rootpath = pathlib.Path(rootpath)
    scmdir = rootpath.joinpath('.hg')
    return scmdir.exists() and scmdir.is_dir()


def describe(rootpath):
    __, error = utils.run('hg help'.split(), cwd=rootpath)
    if error is not None:
        return None, 'Command [hg] is not available'

    description, error = identify_repo(rootpath)
    if error is not None:
        return None, error

    branch, revision, tags, dirty = description

    distance = 0
    changes = 0

    if tags:
        # clean tag found
        version_tag = tags[0]
    elif revision == '0' * 12:
        # initial or no repository state
        version_tag = None
    else:
        # general case
        version_tag, error = find_latest_version_tag(rootpath)
        if error is not None:
            return None, error
        distance, error = graph_distance(rootpath, version_tag)
        if error is not None:
            return None, error
        if version_tag is not None:
            changes, error = changes_since_tag(rootpath, version_tag)
            if error is not None:
                return None, error

    assert changes <= distance, "Unexpected changes / distance meaning"

    scminfo = dict(
        scm='hg',
        revision=revision,
        branch=branch,
        tag=version_tag,
        distance=distance,
        changes=changes,
        dirty=dirty,
    )
    return scminfo, None


def identify_repo(rootpath):
    output, error = utils.run(
        'hg id --id --branch --tags'.split(),
        cwd=rootpath,
        errormsg='Failed to identify repository info',
    )
    if error is not None:
        return None, error

    revision, branch, *tags = output.split()
    # TODO: enable tag prefixes (similar to parsing inside setuptools_scm)

    # not interested in 'tip'
    tags = [tag for tag in tags if tag != 'tip']

    dirty = False
    if revision[-1] == '+':
        dirty = True
        revision = revision[:-1]

    return (branch, revision, tags, dirty), None


def find_latest_version_tag(rootpath):
    """Gets all tags containing a '.' from oldest to newest"""
    output, error = utils.run(
        ["hg", "log", "-r", r"ancestors(.) and tag('re:\.')", "--template", "{tags}\n"],
        cwd=rootpath,
        errormsg='Failed to find latest tag',
    )
    if error is not None:
        return None, error
    if not output:
        return None, None
    tags = output.split()
    latest_tag = tags[-1].split()[-1]
    return latest_tag, None


def graph_distance(rootpath, rev1, rev2="."):
    if rev1 is None:
        rev1 = 'null'
    if rev2 is None:
        rev2 = 'null'
    output, error = utils.run(
        ["hg", "log", "-q", "-r", "{}::{}".format(rev1, rev2)],
        cwd=rootpath,
        errormsg='Failed to find graph distance',
    )
    if error is not None:
        return None, error
    distance = len(output.splitlines()) - 1
    return distance, None


def changes_since_tag(rootpath, tag):
    assert tag
    revset = (
        r"(branch(.)"  # look for revisions in this branch only
        r" and tag({tag!r})::."  # after the last tag
        # ignore commits that only modify .hgtags and nothing else:
        r" and (merge() or file('re:^(?!\.hgtags).*$'))"
        r" and not tag({tag!r}))"  # ignore the tagged commit itself
    ).format(tag=tag)
    output, error = utils.run(
        ["hg", "log", "-r", revset, "--template", "{node|short}\n"],
        cwd=rootpath,
        errormsg='Failed to detect changes since tag',
    )
    if error is not None:
        return None, error
    number_of_commits = len(output.splitlines())
    return number_of_commits, None
