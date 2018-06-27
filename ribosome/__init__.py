#!/usr/bin/env python3

import logging
import sys
import re
import datetime as dt
import functools
import subprocess
import glob
import os
import shutil
import tempfile
import pathlib
import io
import time

import click
import coloredlogs
import ruamel.yaml as ryaml
import boto3
import botocore
import fabric
import fabric.api as fapi

from ribosome import scminfo

# TODO: move from [fabric] to [paramiko]

__version__ = '0.1.0a1'

log = logging.getLogger('ribosome')


def as_object(obj):

    class DictProxy(dict):
        def __getattr__(self, name):
            # attention: returns None for keys that non exist
            # this is convenient for our specific purpose - config files without validation
            # but almost surely is a bad design decision
            return self.get(name)

    if isinstance(obj, dict):
        return DictProxy({k: as_object(v) for k, v in obj.items()})
    elif isinstance(obj, (list, tuple)):
        return [as_object(item) for item in obj]
    else:
        return obj


def unwrap_or_panic(f):

    @functools.wraps(f)
    def decorator(*args, **kwargs):
        result, error = f(*args, **kwargs)
        if error is not None:
            log.error(error)
            sys.exit(1)
        return result

    return decorator


CODONS_TEMPLATE = """
project:
  tag: {project_tag}

meta:
  # format: python

codestyle:
  commands:
    # - make codestyle

build:
  commands:
    # - make build

test:
  commands:
    # - make test

release:
  include:
    # - meta.py
  publish:
    # s3bucket: <bucket-name>
    # localdir: ..

setup:
  commands:
    # - make setup

# [service] and [config] variables will be formatted into command string
# will run with sudo
service:
  load:
    commands:
      # - pipenv run ./service.py install {{service}} {{config}}
      # - pipenv run ./service.py start {{service}} {{config}}
  unload:
    commands:
      # - pipenv run ./service.py uninstall {{service}} {{config}}

services:
  # <service_name>:
  #   configs:
  #     - <config1>
  #     - <config2>
"""


def get_clean_codons(project_tag):
    return CODONS_TEMPLATE.format(project_tag=project_tag)


@unwrap_or_panic
def get_codons():
    log.debug('Reading codons...')
    yaml = ryaml.YAML()
    try:
        with io.open('codons.yaml', encoding='utf-8') as istream:
            codons = yaml.load(istream)
    except FileNotFoundError:
        return None, 'Failed to find codons file: codons.yaml'
    except Exception as e:
        return None, 'Failed to get codons: {}'.format(e)
    else:
        return as_object(codons), None


@unwrap_or_panic
def scm_describe(root='.'):
    log.debug('Getting SCM repository info...')
    # TODO: get outgoing changes status info
    info, error = scminfo.describe(root)
    if error is not None:
        return None, error
    log.info('Got SCM info: %s', info)
    return as_object(info), None


# SCM tag is expected to be special case of PEP-440 version scheme.
# Forms allowed: N.N.N, N.N.NaN, N.N.NbN, N.N.NrcN
SCM_TAG_PATTERN = re.compile(r'^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$')


def is_valid_version(s):
    return re.match(SCM_TAG_PATTERN, s) is not None


@unwrap_or_panic
def check_for_valid_version(s):
    if not is_valid_version(s):
        return None, 'Invalid version string: {}'.format(s)
    return None, None


@unwrap_or_panic
def derive_version_string(scm_info):
    tag = scm_info.tag
    if not tag:
        return None, 'SCM tag is undefined'
    if not is_valid_version(tag):
        return None, 'Invalid SCM tag: {}'.format(tag)
    vs = tag
    if scm_info.changes > 0:
        vs += '.post{}'.format(scm_info.distance)
        if scm_info.revision:
            vs += '+{}'.format(scm_info.revision)
    if scm_info.dirty:
        vs += '.d{:%Y%m%d}'.format(dt.datetime.now(dt.timezone.utc))
    return vs, None


META_PYTHON = """
# This file is generated. Do not edit or store under version control.

project = {project}
version = {version}

revision = {revision}
branch = {branch}
tag = {tag}
distance = {distance}
changes = {changes}
dirty = {dirty}

generated = {generated}
"""

META_SPEC = {
    'python': ('meta.py', META_PYTHON),
}


def fmt_python(obj):
    if isinstance(obj, bool) or isinstance(obj, int):
        return "{}".format(obj)
    elif isinstance(obj, dt.datetime):
        return "'{}'".format(obj.isoformat())
    else:
        return "'{}'".format(obj)


@unwrap_or_panic
def write_meta(codons, version, scm_info):
    log.debug('Writing meta descriptor file...')
    generated = dt.datetime.now(dt.timezone.utc)
    metadata = dict(
        project=codons.project.tag,
        version=version,
        revision=scm_info.revision,
        branch=scm_info.branch,
        tag=scm_info.tag,
        distance=scm_info.distance,
        changes=scm_info.changes,
        dirty=scm_info.dirty,
        generated=generated,
    )
    metadata = {k: fmt_python(v) for k, v in metadata.items()}
    if codons.meta.format not in META_SPEC:
        return None, 'Unsupported meta descriptor format: {}'.format(codons.meta.format)
    filename, template = META_SPEC[codons.meta.format]
    try:
        with io.open(filename, 'w', encoding='utf-8') as ostream:
            ostream.write(template.format(**metadata))
    except Exception as e:
        return None, 'Failed to write {}: {}'.format(filename, e)
    return filename, None


def job_id_generator():
    n = 1
    while True:
        yield n
        n += 1


JOB_ID_SEQUENCE = job_id_generator()


def next_job_id():
    return next(JOB_ID_SEQUENCE)


@unwrap_or_panic
def run_job(args, env=None):
    job_id = next_job_id()
    command = ' '.join(args)
    log.debug('[job #%d] run: %s', job_id, command)
    joblog = logging.getLogger('job:{}'.format(args[0]))
    try:
        job_env = os.environ
        if env is not None:
            job_env.update(env)
        job = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=job_env)
        while True:
            job_finished = job.poll() is not None
            output = job.stdout.readline()
            output_consumed = not output
            if job_finished and output_consumed:
                break
            output = output.decode('utf-8', 'replace').rstrip()
            if output:
                joblog.trace(output)
    except Exception as e:
        return None, 'Failed to run job #{} [{}]: {}'.format(job_id, command, e)
    else:
        if job.returncode != 0:
            log.debug('[job #%d] finished with return code %s', job_id, job.returncode)
        return job.returncode, None


@unwrap_or_panic
def check_scm_status_for_release(scm_info):
    clean = True
    if scm_info.dirty:
        log.warn('Working directory has uncommitted changes. Think what you\'re doing!')
        clean = False
    if scm_info.distance is not None:
        log.warn('Working directory not tagged. Found %d commit(s) after last tag. Think what you\'re doing!', scm_info.distance)
        clean = False
    if not clean:
        TIME_TO_THINK = 5  # seconds
        time.sleep(TIME_TO_THINK)
    return None, None


@unwrap_or_panic
def run_commands(job_name, commands):
    if not commands:
        return None, None
    log.debug('%s...', job_name)
    for command in commands:
        returncode = run_job(command.split())
        if returncode != 0:
            return None, '{} failed'.format(job_name)
    return None, None


@unwrap_or_panic
def make_scm_archive(target_dir):
    current_dir = pathlib.Path.cwd()
    if current_dir.joinpath('.hg').exists():
        args = ['hg', 'archive', target_dir]
        returncode = run_job(args)
        if returncode != 0:
            return None, 'Making SCM archive failed'
    elif current_dir.joinpath('.git').exists():
        with tempfile.TemporaryDirectory() as tempdir:
            archive = os.path.join(tempdir, 'archive.tar')
            args = ['git', 'archive', '--format=tar', '--output={}'.format(archive), 'HEAD']
            returncode = run_job(args)
            if returncode != 0:
                return None, 'Making SCM archive failed'
            args = ['tar', '--extract', '--file={}'.format(archive), '--directory={}'.format(target_dir)]
            returncode = run_job(args)
            if returncode != 0:
                return None, 'Making SCM archive failed'
    else:
        return None, 'No .git or .hg repository found'
    return None, None


def derive_release_name(project_tag, version):
    return '{}-{}'.format(project_tag, version)


def derive_archive_name(release_name):
    return '{}.tar.xz'.format(release_name)


def derive_s3_path(project_tag, release_name):
    release_archive_name = derive_archive_name(release_name)
    return '{}/{}'.format(project_tag, release_archive_name)


def is_s3_object_exists(s3, bucket, name):
    try:
        s3.Object(bucket, name).load()
    except botocore.exceptions.ClientError as e:
        if e.response['Error']['Code'] == "404":
            return False
        raise
    else:
        return True


@unwrap_or_panic
def publish_release(codons, release_name, force=False):

    def additional_files(codons):
        files_to_include = []
        if codons.release.include:
            for pattern in codons.release.include:
                len_before = len(files_to_include)
                for path in glob.glob(pattern, recursive=True):
                    if os.path.exists(path):
                        files_to_include.append(path)
                    else:
                        log.warn('Path not exists: %s', path)
                len_after = len(files_to_include)
                if len_before == len_after:
                    log.warn('No files found by pattern: %s', pattern)
        return files_to_include

    def copyfiles(paths, targetroot):
        for localpath in paths:
            targetpath = os.path.join(targetroot, localpath)
            targetpath = os.path.normpath(targetpath)
            targetdir, filename = os.path.split(targetpath)
            if not os.path.isdir(targetdir):
                os.makedirs(targetdir)
            if os.path.isdir(localpath):
                continue
            shutil.copy(localpath, targetdir)

    @unwrap_or_panic
    def make_archive(srcdir, targetfilepath):
        srcdir = os.path.normpath(srcdir)
        parent, name = os.path.split(srcdir)
        args = ['tar', '--create', '--xz', '--directory', parent, '--file', targetfilepath, name]
        env = {'XZ_OPT': '-9'}
        returncode = run_job(args, env=env)
        if returncode != 0:
            return None, 'Making release archive failed'
        return None, None

    if not codons.release.publish:
        return None, 'Codons for publishing not found. Nothing to do for release'

    project_tag = codons.project.tag

    release_archive_name = derive_archive_name(release_name)
    release_s3_path = derive_s3_path(project_tag, release_name)

    with tempfile.TemporaryDirectory() as tempdir:
        log.debug('Making artifacts...')
        targetroot = os.path.join(tempdir, release_name)
        os.makedirs(targetroot)
        make_scm_archive(targetroot)
        log.debug('Copying additional include files...')
        paths = additional_files(codons)
        copyfiles(paths, targetroot)
        release_archive_path = os.path.join(tempdir, release_archive_name)
        log.debug('Compressing...')
        make_archive(targetroot, release_archive_path)
        if codons.release.publish.localdir:
            log.debug('Publishing to local directory...')
            localdir = codons.release.publish.localdir
            if not os.path.isdir(localdir):
                return None, 'Invalid local directory for publishing: {}'.format(localdir)
            if not force and os.path.exists(os.path.join(localdir, release_archive_name)):
                return None, 'Release [{}] already published at local directory [{}]'.format(release_name, localdir)
            shutil.copy(release_archive_path, localdir)
        if codons.release.publish.s3bucket:
            log.debug('Publishing to Amazon S3...')
            s3bucket = codons.release.publish.s3bucket
            s3 = boto3.resource('s3')
            if not force and is_s3_object_exists(s3, s3bucket, release_s3_path):
                return None, 'Release [{}] already published at Amazon S3 bucket [{}]'.format(release_name, s3bucket)
            s3.Object(s3bucket, release_s3_path).upload_file(release_archive_path)

    return None, None


class LogStream():

    def __init__(self, log_method, strip_prefixes=None):
        self.log_method = log_method
        self.strip_prefixes = strip_prefixes if strip_prefixes is not None else []
        self.buffer = []

    def write(self, text):
        if not text:
            return
        for prefix in self.strip_prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):]
        start = 0
        while True:
            pos = text.find('\n', start)
            if pos == -1:
                self.buffer.append(text[start:])
                break
            else:
                if pos > 0:
                    self.buffer.append(text[start:pos])
                start = pos + 1
                self.flush()

    def flush(self):
        output = ''.join(self.buffer)
        output = output.strip()
        if output:
            self.log_method(output)
        self.buffer = []


def make_remote_operation(fabric_func, *fixed_args, logstreams=False, **fixed_kwargs):

    @unwrap_or_panic
    @functools.wraps(fabric_func)
    def remote_operation(*args, **kwargs):
        host = fapi.env.host_string
        rlog = logging.getLogger('host:{}'.format(host))
        if logstreams:
            strip_prefixes = (
                '[{}] out:'.format(host),
            )
            kwargs['stdout'] = LogStream(rlog.trace, strip_prefixes)
            kwargs['stderr'] = LogStream(rlog.warn, strip_prefixes)
        remote_operation = functools.partial(fabric_func, *fixed_args, **fixed_kwargs)
        rlog.debug('[%s]: %s', fabric_func.__name__, ' '.join(map(str, args)))
        result = remote_operation(*args, **kwargs)
        if result.failed:
            if hasattr(result, 'return_code'):
                if result.return_code > 1:
                    # http://tldp.org/LDP/abs/html/exitcodes.html
                    return None, 'Host [{}] failed to run command: {}'.format(host, args[0])
        return result, None

    return remote_operation


remote_run = make_remote_operation(fapi.run, logstreams=True, shell=True)
remote_sudo = make_remote_operation(fapi.sudo, logstreams=True, shell=True)
remote_put = make_remote_operation(fapi.put)
remote_get = make_remote_operation(fapi.get)


def is_remote_path_exists(filepath, isdir=False, check_read_permission=False):
    check_symbol = 'd' if isdir else 'f'
    result = remote_run('test -{} {}'.format(check_symbol, filepath))
    if result.failed:
        return False, None
    if check_read_permission:
        result = remote_run('test -r {}'.format(filepath))
        if result.failed:
            return False, None
    return True, None


RELEASES_REMOTE_ROOT = pathlib.PurePosixPath('~/releases')
PROJECTS_REMOTE_ROOT = pathlib.PurePosixPath('~/projects')


def split_release_name(release_name):
    project_tag, version = release_name.rsplit('-', 1)
    return project_tag, version


@fapi.task
@unwrap_or_panic
def upload_release(host, release_name, s3bucket, force=False):
    log.debug('Starting release upload process...')

    release_archive_name = derive_archive_name(release_name)
    project_tag, version = split_release_name(release_name)
    release_s3_path = derive_s3_path(project_tag, release_name)

    s3 = boto3.resource('s3')

    log.debug('Checking artifacts...')

    if not is_s3_object_exists(s3, s3bucket, release_s3_path):
        return None, 'Release [{}] not found at S3 bucket [{}]'.format(release_name, s3bucket)

    @unwrap_or_panic
    def ensure_dir_exists(dirpath):
        result = remote_run('mkdir -p {}'.format(dirpath))
        if result.failed:
            return None, 'Failed to make directories'
        return None, None

    ensure_dir_exists(RELEASES_REMOTE_ROOT)

    release_archive_name = derive_archive_name(release_name)
    remote_release_archive_path = RELEASES_REMOTE_ROOT.joinpath(release_archive_name)

    release_archive_exists, error = is_remote_path_exists(remote_release_archive_path, check_read_permission=True)
    if error is not None:
        return None, error

    if not force and release_archive_exists:
        # TODO: file checksum compare
        log.info('Release [%s] already found uploaded at remote host [%s]: %s', release_name, host, remote_release_archive_path)
        log.warn('No checksum checking was done - trusting the remote file blindly')
    else:
        with tempfile.TemporaryDirectory() as tempdir:
            local_release_archive_path = os.path.join(tempdir, release_archive_name)
            log.debug('Downloading release from Amazon S3...')
            s3.Object(s3bucket, release_s3_path).download_file(local_release_archive_path)
            log.debug('Downloaded to: %s', local_release_archive_path)
            log.debug('Uploading release archive to host [%s]...', host)
            result = remote_put(local_release_archive_path, str(remote_release_archive_path))
            if result.failed:
                return None, 'Failed to upload release archive to remote host'

    remote_project_root = PROJECTS_REMOTE_ROOT.joinpath(project_tag)

    log.debug('Extracting release archive to deploy location...')
    ensure_dir_exists(remote_project_root)
    result = remote_run('tar --extract --directory {} --file {}'.format(remote_project_root, remote_release_archive_path))
    if result.failed:
        return None, 'Failed to extract release archive at remote host'

    return None, None


@fapi.task
@unwrap_or_panic
def setup_runtime_environment(host, release_name, commands):
    if not commands:
        return None, None
    log.debug('Starting runtime environment setup...')
    project_tag, version = split_release_name(release_name)
    remote_release_root = PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    with fapi.cd(str(remote_release_root)):
        for command in commands:
            result = remote_run(command)
            if result.failed:
                return None, 'Failed to setup runtime environment by: {}'.format(command)
    return None, None


SERVICE_INDEX_NAME = 'services.index.yaml'


@fapi.task
@unwrap_or_panic
def update_services_index(host, release_name, service, config, include=None):
    assert include is not None
    project_tag, version = split_release_name(release_name)
    remote_project_root = PROJECTS_REMOTE_ROOT.joinpath(project_tag)
    service_index_path = remote_project_root.joinpath(SERVICE_INDEX_NAME)

    service_index = {}

    service_index_exists, error = is_remote_path_exists(service_index_path, check_read_permission=True)
    if error is not None:
        return None, error

    yaml = ryaml.YAML()
    yaml.default_flow_style = False

    if service_index_exists:
        # text mode stream or StringIO not working here - don't know why
        with tempfile.TemporaryFile() as stream:
            remote_get(str(service_index_path), stream)
            stream.seek(0)
            try:
                service_index = yaml.load(stream)['services']
            except Exception as e:
                log.warn('Service index corrupted: %s', e)
                log.warn('Creating new blank service index')
                service_index = {}

    if service not in service_index:
        service_index[service] = {}

    if include:
        service_index[service][config] = version
    else:
        service_index[service].pop(config, None)
        if not service_index[service]:
            service_index.pop(service, None)

    def make_dict(obj):
        if isinstance(obj, dict):
            return {k: make_dict(v) for k, v in obj.items()}
        else:
            return obj

    service_index = make_dict(service_index)

    buf = io.StringIO()
    yaml.dump(dict(services=service_index), buf)
    remote_put(buf, str(service_index_path))

    return None, None


def remote_list_subdirs(host, dirpath):
    # expand user directory [~]
    r = remote_run('echo {}'.format(PROJECTS_REMOTE_ROOT))
    projects_abspath = pathlib.PurePosixPath(str(r))
    sftp = fabric.sftp.SFTP(host)
    r = sftp.ftp.listdir(str(projects_abspath))
    subdirs = [name for name in r if sftp.isdir(str(projects_abspath.joinpath(name)))]
    return subdirs


@fapi.task
@unwrap_or_panic
def get_services_index(host, project_tag):

    if project_tag:
        project_tags = [project_tag]
    else:
        project_tags = remote_list_subdirs(host, PROJECTS_REMOTE_ROOT)

    service_indices = {}

    for ptag in project_tags:

        remote_project_root = PROJECTS_REMOTE_ROOT.joinpath(ptag)
        service_index_path = remote_project_root.joinpath(SERVICE_INDEX_NAME)

        service_index_exists, error = is_remote_path_exists(service_index_path, check_read_permission=True)
        if error is not None:
            return None, error

        if not service_index_exists:
            continue

        yaml = ryaml.YAML()

        # text stream or StringIO not working here
        with tempfile.TemporaryFile() as stream:
            remote_get(str(service_index_path), stream)
            stream.seek(0)
            try:
                service_index = yaml.load(stream)['services']
            except Exception as e:
                return None, 'Service index of project [{}] corrupted: {}'.format(ptag, e)
            else:
                service_index = dict(service_index)
                service_indices[ptag] = service_index

    return service_indices, None


@fapi.task
@unwrap_or_panic
def get_remote_codons(host, project_tag, release_name):

    remote_release_root = PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    codons_path = remote_release_root.joinpath('codons.yaml')

    codons_exists, error = is_remote_path_exists(codons_path, check_read_permission=True)
    if error is not None:
        return None, error
    if not codons_exists:
        return None, 'Codons not found for release [{}] at host [{}]'.format(release_name, host)

    yaml = ryaml.YAML()

    # text stream or StringIO not working here
    with tempfile.TemporaryFile() as stream:
        remote_get(str(codons_path), stream)
        stream.seek(0)
        try:
            codons = yaml.load(stream)
        except Exception as e:
            return None, 'Codons corrupted for release [{}] at host [{}]: {}'.format(release_name, host, e)
        else:
            return as_object(codons), None


@fapi.task
@unwrap_or_panic
def run_service_commands(host, project_tag, release_name, service, config, commands, sudo=False):
    if not commands:
        return None, None
    runner = remote_sudo if sudo else remote_run
    remote_release_root = PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    with fapi.cd(str(remote_release_root)):
        for command in commands:
            command = command.format(service=service, config=config)
            result = runner(command)
            if result.failed:
                return None, 'Failed to run service command: {}'.format(command)
    return None, None


@fapi.task
@unwrap_or_panic
def load_service(host, project_tag, release_name, service, config):
    log.debug('Loading service...')
    codons = get_remote_codons(host, project_tag, release_name)
    if not codons.service or not codons.service.load:
        return None, 'Service load commands not found'
    load_commands = codons.service.load.get('commands', [])
    run_service_commands(host, project_tag, release_name, service, config, load_commands, sudo=True)
    update_services_index(host, release_name, service, config, include=True)
    return None, None


@fapi.task
@unwrap_or_panic
def unload_service(host, project_tag, release_name, service, config):
    log.debug('Unloading service...')
    codons = get_remote_codons(host, project_tag, release_name)
    if not codons.service or not codons.service.unload:
        return None, 'Service unload commands not found'
    unload_commands = codons.service.unload.get('commands', [])
    run_service_commands(host, project_tag, release_name, service, config, unload_commands, sudo=True)
    update_services_index(host, release_name, service, config, include=False)
    return None, None


def find_service_configs(codons, services_pattern, configs_pattern):

    def derive_regexp(pattern):
        prefix = r''
        suffix = r''
        if pattern.startswith(r'*'):
            prefix = r'\w+'
            pattern = pattern[1:]
        regexp = r'^' + prefix + pattern.replace(r'*', r'\w*') + suffix + r'$'
        return re.compile(regexp)

    services_regexps = [derive_regexp(pattern) for pattern in services_pattern.split(',')]
    configs_regexps = [derive_regexp(pattern) for pattern in configs_pattern.split(',')]

    matches = []
    for service, descriptor in codons.services.items():
        if any(pattern.match(service) for pattern in services_regexps):
            for config in descriptor.get('configs', []):
                if any(pattern.match(config) for pattern in configs_regexps):
                    matches.append((service, config))
    return matches


def execute_as_remote_task(operation, host, *args, **kwargs):
    result = fapi.execute(functools.partial(operation, host, *args, **kwargs), hosts=[host])
    return result[host]


def fabric_global_patch():
    fapi.env.use_ssh_config = True
    fabric.state.env.warn_only = True
    fabric.state.output.warnings = False
    fabric.state.output.running = False


def setup_global_remote_sudo_password(password):
    fapi.env.sudo_password = password


def logging_global_patch():
    logging.TRACE = logging.DEBUG - 1
    logging.addLevelName(logging.TRACE, 'TRACE')

    def log_trace(self, message, *args, **kws):
        if self.isEnabledFor(logging.TRACE):
            self._log(logging.TRACE, message, args, **kws)

    logging.Logger.trace = log_trace
    coloredlogs.DEFAULT_LEVEL_STYLES['trace'] = dict(color='blue')


def setup_logging(verbose=True):
    console_level = logging.TRACE if verbose else logging.INFO
    console_format = '[%(name)-20s] %(levelname)-8s %(message)s'
    console_formatter = coloredlogs.ColoredFormatter(console_format)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(console_formatter)
    file_format = '%(asctime)s.%(msecs)03d [%(name)-20s] %(levelname)-8s %(message)s'
    file_formatter = logging.Formatter(file_format)
    file_handler = logging.FileHandler('ribosome.log', 'w', encoding='utf-8')
    file_handler.setLevel(logging.TRACE)
    file_handler.setFormatter(file_formatter)
    root = logging.getLogger()
    root.setLevel(logging.TRACE)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    logging.getLogger('botocore').setLevel(logging.WARN)
    logging.getLogger('boto3').setLevel(logging.WARN)
    logging.getLogger('s3transfer').setLevel(logging.WARN)
    logging.getLogger('paramiko').setLevel(logging.WARN)


def welcome():
    log.info('Ribosome {}. Let\'s make a bit of proteins...'.format(__version__))


def read_project_codons():
    codons = get_codons()
    log.info('Found project: %s', codons.project.tag)
    return codons


def process_errors(f):

    @functools.wraps(f)
    def decorated(*args, **kwds):
        try:
            return f(*args, **kwds)
        except Exception as e:
            verbose = True
            ctx = click.get_current_context(silent=True)
            if ctx is not None:
                ctx = ctx.find_root()
                verbose = ctx.params.get('verbose', True)
            log.fatal(e, exc_info=verbose)
            sys.exit(1)

    return decorated


CLICK_CONTEXT_SETTINGS = dict(
    max_content_width=140,
)


@click.group(context_settings=CLICK_CONTEXT_SETTINGS)
@click.option('-v', '--verbose', is_flag=True, help='More detailed logging to console')
@click.option('-f', '--force', is_flag=True, help='Rewrite existing files')
@click.version_option(prog_name='Ribosome', version=__version__)
@click.pass_context
def cli(ctx, verbose, force):
    """Yet another project deploy and release tool"""
    settings = {
        'force': force,
    }
    ctx.obj = as_object(settings)
    setup_logging(verbose=verbose)


@cli.command(short_help='Initialize Ribosome project')
@click.pass_obj
@process_errors
@unwrap_or_panic
def init(settings):
    """Initialize current directory with codons.yaml"""
    welcome()
    current_dir = pathlib.Path.cwd()
    codons_path = current_dir.joinpath('codons.yaml')
    if codons_path.exists():
        return None, 'Current directory already contains codons.yaml'
    project_tag = current_dir.name
    codons_yaml = get_clean_codons(project_tag)
    with io.open(codons_path, 'w', encoding='utf-8') as ostream:
        ostream.write(codons_yaml)
    return None, None


@cli.group()
def version():
    """Show version or update meta descriptor"""
    pass


@version.command('info')
@process_errors
@unwrap_or_panic
def version_info():
    """Show project version information"""
    welcome()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    log.info('Version derived: %s', version)
    return None, None


@version.command('update')
@process_errors
@unwrap_or_panic
def version_update():
    """Update project meta descriptor with current version"""
    welcome()
    codons = read_project_codons()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    log.info('Version derived: %s', version)
    if not codons.meta:
        return None, 'No meta descriptor settings defined'
    filename = write_meta(codons, version, scm_info)
    log.info('Meta descriptor updated: %s', filename)
    return None, None


@cli.command()
@click.pass_obj
@process_errors
@unwrap_or_panic
def release(settings):
    """Make release and publish artifacts"""
    welcome()
    codons = read_project_codons()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    log.info('Version derived: %s', version)
    check_scm_status_for_release(scm_info)
    if codons.meta:
        filename = write_meta(codons, version, scm_info)
        log.info('Meta descriptor updated: %s', filename)
    if codons.codestyle:
        commands = codons.codestyle.get('commands', [])
        run_commands('Checking code style', commands)
        log.info('Code style checked')
    if codons.build:
        commands = codons.build.get('commands', [])
        run_commands('Building project', commands)
        log.info('Project built')
    if codons.test:
        commands = codons.test.get('commands', [])
        run_commands('Running tests', commands)
        log.info('Tests completed')
    if codons.release:
        release_name = derive_release_name(codons.project.tag, version)
        publish_release(codons, release_name, force=settings.force)
        log.info('Release [%s] published', release_name)
    return None, None


@cli.command(short_help='Deploy release artifacts to host')
@click.argument('version')
@click.argument('host')
@click.pass_obj
@process_errors
@unwrap_or_panic
def deploy(settings, version, host):
    """Deploy release artifacts to host and prepare for run.

    \b
    Args:
        version: project version to deploy
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()
    codons = read_project_codons()

    check_for_valid_version(version)

    if not codons.release or not codons.release.publish or not codons.release.publish.s3bucket:
        return None, 'Codons for Amazon S3 not found. Don\'t know how to get artifact for deploying'

    release_name = derive_release_name(codons.project.tag, version)
    s3bucket = codons.release.publish.s3bucket

    execute_as_remote_task(upload_release, host, release_name, s3bucket, force=settings.force)
    log.info('Release uploaded to remote host')

    # pip install --user pipenv
    # .profile

    remote_codons = execute_as_remote_task(get_remote_codons, host, codons.project.tag, release_name)

    if remote_codons.setup:
        setup_commands = remote_codons.setup.get('commands', [])
        execute_as_remote_task(setup_runtime_environment, host, release_name, setup_commands)
        log.info('Runtime environment at remote host configured')

    log.info('Release [%s] successfully deployed at host [%s]', release_name, host)
    return None, None


@cli.command(short_help='Load service at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('version')
@click.argument('service', type=str)
@click.argument('config', type=str)
@click.argument('host')
@click.pass_obj
@process_errors
@unwrap_or_panic
def load(settings, password, version, service, config, host):
    """Install and run service with chosen configuration.

    \b
    Args:
        version: project version to use
        service: service name
        config: configuration name
        host: destination host alias (usage of ssh config assumed)
    """
    setup_global_remote_sudo_password(password)
    welcome()
    local_codons = read_project_codons()

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    codons = execute_as_remote_task(get_remote_codons, host, project_tag, release_name)
    matches = find_service_configs(codons, service, config)
    if not matches:
        log.info('No matched service configs found: nothing to do')
        return None, None

    log.debug('Service configs matched: %s', matches)

    for service, config in matches:
        log.debug('Processing service [%s] configuration [%s]...', service, config)
        if service in project_service_index and config in project_service_index[service]:
            old_version_loaded = project_service_index[service][config]
            old_service_release_name = derive_release_name(project_tag, old_version_loaded)
            execute_as_remote_task(unload_service, host, project_tag, old_service_release_name, service, config)
            log.info('Service [%s] configuration [%s] from release [%s] unloaded at host [%s]', service, config, old_service_release_name, host)
        execute_as_remote_task(load_service, host, project_tag, release_name, service, config)
        log.info('Service [%s] configuration [%s] from release [%s] loaded at host [%s]', service, config, release_name, host)

    return None, None


@cli.command(short_help='Unload service at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('version')
@click.argument('service')
@click.argument('config')
@click.argument('host')
@click.pass_obj
@process_errors
@unwrap_or_panic
def unload(settings, password, version, service, config, host):
    """Stop and uninstall service with chosen configuration.

    \b
    Args:
        version: project version to use
        service: service name
        config: configuration name
        host: destination host alias (usage of ssh config assumed)
    """
    setup_global_remote_sudo_password(password)
    welcome()
    local_codons = read_project_codons()

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    codons = execute_as_remote_task(get_remote_codons, host, project_tag, release_name)
    matches = find_service_configs(codons, service, config)
    if not matches:
        log.info('No matched service configs found: nothing to do')
        return None, None

    log.debug('Service configs matched: %s', matches)

    for service, config in matches:
        log.debug('Processing service [%s] configuration [%s]...', service, config)
        if service in project_service_index and config in project_service_index[service]:
            version_loaded = project_service_index[service][config]
            if version != version_loaded:
                log.warn('Skipping unload service [%s] config [%s]: version loaded [%s] does not match requested [%s]', service, config, version_loaded, version)
                continue
        execute_as_remote_task(unload_service, host, project_tag, release_name, service, config)
        log.info('Service [%s] configuration [%s] from release [%s] unloaded at host [%s]', service, config, release_name, host)

    return None, None


@cli.command(short_help='Run command for service at remote host')
@click.argument('version')
@click.argument('service')
@click.argument('config')
@click.argument('action')
@click.argument('args', nargs=-1)
@click.argument('host')
@click.pass_obj
@process_errors
@unwrap_or_panic
def run(settings, version, service, config, action, args, host):
    """Run action for service with chosen configuration.

    \b
    Args:
        version: project version to use
        service: service name
        config: configuration name
        action: action to run
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()
    log.debug('host: %s', host)
    log.debug('action: %s, args: %s', action, args)
    log.error('Not implemented')
    return None, None


@cli.command(short_help='Show loaded services at remote host')
@click.option('-a', '--all', 'search_all_projects', is_flag=True, help='Search through all projects')
@click.argument('host')
@click.pass_obj
@process_errors
@unwrap_or_panic
def show(settings, search_all_projects, host):
    """Show loaded services at remote host.

    \b
    Args:
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()

    log.info('Inspecting services at host [%s]...', host)

    if search_all_projects:
        project_tag = None
    else:
        codons = read_project_codons()
        project_tag = codons.project.tag

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)

    if service_indices is None:
        if search_all_projects:
            log.info('No services indices found')
        else:
            log.info('No services index for project [%s] found', project_tag)
    else:
        if service_indices:
            for ptag, services_index in service_indices.items():
                if services_index:
                    log.info('Project [%s]: services loaded:', ptag)
                    index = []
                    for service, configs in services_index.items():
                        index.extend((service, config, version) for config, version in configs.items())
                    index = sorted(index)
                    for service, config, version in index:
                        log.info('    %s/%s: %s', service, config, version)
                else:
                    log.info('Project [%s]: no loaded services found', ptag)
        else:
            log.info('No loaded services found')

    return None, None


fabric_global_patch()
logging_global_patch()


if __name__ == '__main__':
    cli()
