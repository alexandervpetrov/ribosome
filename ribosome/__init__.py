#!/usr/bin/env python

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
import importlib
import collections
import json

import click
import coloredlogs
import ruamel.yaml as ryaml
import boto3
import botocore
import fabric
import fabric.api as fapi
import requests

from ribosome import (
    constants,
    scmtools,
)

__version__ = '0.6.2'

log = logging.getLogger('ribosome')


def as_object(obj):

    class DictProxy(dict):
        def __getattr__(self, name):
            # attention: returns None for keys that non exist
            # this is convenient for our specific purpose - config files without validation
            # but almost surely is a bad design decision
            return self.get(name)

    if isinstance(obj, collections.abc.Mapping):
        return DictProxy({k: as_object(v) for k, v in obj.items()})
    elif isinstance(obj, collections.abc.Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [as_object(item) for item in obj]
    else:
        return obj


class RibosomeError(Exception):
    pass


class CodonsError(RibosomeError):
    pass


class TagPolicyError(RibosomeError):
    pass


class JobError(RibosomeError):
    pass


class RemoteError(RibosomeError):
    pass


def unwrap_or_panic(f):

    @functools.wraps(f)
    def decorator(*args, **kwargs):
        result, error = f(*args, **kwargs)
        if error is not None:
            log.error(error)
            sys.exit(1)
        return result

    return decorator


def scm_describe(root='.'):
    log.debug('Getting SCM repository info...')
    # TODO: get outgoing changes status info
    info = scmtools.describe(root)
    scminfo = as_object(info)
    log.info('Got SCM info: %s', scminfo)
    return scminfo


def derive_version_string(scm_info):
    tag = scm_info.tag
    if not tag:
        raise RibosomeError('SCM tag is undefined')
    vs = tag
    if scm_info.distance > 0:
        vs += '.post{}'.format(scm_info.distance)
        if scm_info.revision:
            vs += '+{}'.format(scm_info.revision)
    if scm_info.dirty:
        vs += '.d{:%Y%m%d}'.format(dt.datetime.now(dt.timezone.utc))
    log.info('Version derived: %s', vs)
    return vs


def read_project_codons():
    log.debug('Reading codons...')
    yaml = ryaml.YAML()
    try:
        with io.open('codons.yaml', encoding='utf-8') as istream:
            codons = yaml.load(istream)
    except FileNotFoundError as e:
        raise CodonsError('Failed to find file: codons.yaml') from e
    except Exception as e:
        raise CodonsError('Failed to read codons: {}'.format(e)) from e
    else:
        codons = as_object(codons)
        log.info('Found project: %s', codons.project.tag)
        return codons


def default_tag_policy(tag):
    if not tag:
        return False
    is_release = re.match(constants.RELEASE_TAG_PATTERN, tag) is not None
    is_development = re.match(constants.DEVELOPMENT_TAG_PATTERN, tag) is not None
    is_allowed_for_release = is_release or is_development
    return is_allowed_for_release


def any_tag_policy(tag):
    if not tag:
        return False
    return True


def load_tag_policy(codons):
    tag_policy_descriptor = codons.tag_policy
    if not tag_policy_descriptor or '.' not in tag_policy_descriptor:
        raise TagPolicyError('Invalid tag policy descriptor: {}'.format(tag_policy_descriptor))
    module_s, function_s = tag_policy_descriptor.rsplit('.', 1)
    if not module_s or not function_s:
        raise TagPolicyError('Invalid tag policy descriptor: {}'.format(tag_policy_descriptor))
    try:
        module = importlib.import_module(module_s)
    except Exception as e:
        raise TagPolicyError('Failed to load tag policy descriptor: {}'.format(e)) from e
    tag_policy = getattr(module, function_s, None)
    if tag_policy is None or not callable(tag_policy):
        raise TagPolicyError('Tag policy descriptor does not represent callable: {}'.format(tag_policy_descriptor))
    return tag_policy


def report_to_slack(webhook_url, msg):
    json_data = json.dumps(dict(text=msg))
    try:
        r = requests.post(webhook_url, data=json_data, timeout=2)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.warning('Failed to report to Slack: %s', e)


def load_hooks(codons):
    hooks = {
        'report': None,
    }
    if codons.slack:
        webhook_url = codons.slack.get('incoming_webhook_url')
        if webhook_url:
            hooks['report'] = functools.partial(report_to_slack, webhook_url)
    return as_object(hooks)


def report(hooks, msg, *args):
    if hooks.report:
        hooks.report(msg % args)


def encode_python(obj):
    if isinstance(obj, bool) or isinstance(obj, int):
        return "{}".format(obj)
    elif isinstance(obj, dt.datetime):
        return "'{}'".format(obj.isoformat())
    else:
        return "'{}'".format(obj)


class CustomJSONEncoder(json.JSONEncoder):

    def default(self, obj):
        if isinstance(obj, dt.datetime):
            return obj.isoformat()
        return json.JSONEncoder.default(self, obj)


def write_meta(codons, version, scm_info):
    log.debug('Writing meta descriptor file(s)...')
    generated = dt.datetime.now(dt.timezone.utc)
    metadata = dict(
        project=codons.project.tag,
        version=version,
        revision=scm_info.revision,
        branch=scm_info.branch,
        tag=scm_info.tag,
        distance=scm_info.distance,
        dirty=scm_info.dirty,
        generated=generated,
    )
    if not codons.meta.format:
        raise CodonsError("No meta descriptor format specified")
    formats = [token.strip() for token in codons.meta.format.split(',')]
    for fmt in formats:
        if fmt == 'python':
            filename = 'meta.py'
            data = {k: encode_python(v) for k, v in metadata.items()}
            meta_output = constants.META_PYTHON.format(**data)
        elif fmt == 'json':
            filename = 'meta.json'
            data = dict(_comment='This file is generated. Do not edit or store under version control')
            data.update(metadata)
            meta_output = json.dumps(data, indent=4, cls=CustomJSONEncoder)
            meta_output += '\n'
        else:
            log.warning('Unsupported meta descriptor format: %s', fmt)
            continue
        try:
            with io.open(filename, 'w', encoding='utf-8') as ostream:
                ostream.write(meta_output)
        except Exception as e:
            raise RibosomeError('Failed to write {}: {}'.format(filename, e)) from e
        else:
            log.info('Meta descriptor updated: %s', filename)


def job_id_generator():
    n = 1
    while True:
        yield n
        n += 1


JOB_ID_SEQUENCE = job_id_generator()


def next_job_id():
    return next(JOB_ID_SEQUENCE)


def run_job(args, env=None, errormsg=None, check=True):
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
        log.warning('[job #%d] failed with error: %s', job_id, e)
        if errormsg:
            msg = '{}: {}'.format(errormsg, e)
        else:
            cmd = args[0]
            msg = 'Failed to run job #{} [{}]: {}'.format(job_id, cmd, e)
        raise JobError(msg) from e
    else:
        if job.returncode != 0:
            if check:
                log.warning('[job #%d] finished with nonzero return code: %s', job_id, job.returncode)
                if errormsg:
                    msg = '{} (return code {})'.format(errormsg, job.returncode)
                else:
                    cmd = args[0]
                    msg = 'Job #{} [{}] fisnihed with nonzero return code: {}'.format(job_id, cmd, job.returncode)
                raise JobError(msg)
            else:
                log.debug('[job #%d] finished with nonzero return code: %s', job_id, job.returncode)


def run_commands(job_name, commands):
    if not commands:
        return
    log.debug('%s: starting...', job_name)
    for command in commands:
        run_job(command.split())
    log.info('%s: done', job_name)


def check_scm_status_for_release(scm_info):
    warnings = []
    if scm_info.dirty:
        warnings.append('Working directory has uncommitted changes')
    if scm_info.distance > 0:
        warnings.append('Working directory not tagged: found {} commit(s) after last tag'.format(scm_info.distance))
    if warnings:
        for w in warnings:
            log.warning(w)
        log.warning('Think what you\'re doing! You can press Ctrl+C to stop')
        TIME_TO_THINK = 7  # seconds
        time.sleep(TIME_TO_THINK)


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


def publish_release(codons, release_name, hooks, force=False):

    def additional_files(codons):
        files_to_include = []
        if codons.release.include:
            for pattern in codons.release.include:
                len_before = len(files_to_include)
                for path in glob.glob(pattern, recursive=True):
                    if os.path.exists(path):
                        files_to_include.append(path)
                    else:
                        log.warning('Path not exists: %s', path)
                len_after = len(files_to_include)
                if len_before == len_after:
                    log.warning('No files found by pattern: %s', pattern)
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

    def make_archive(srcdir, targetfilepath):
        srcdir = os.path.normpath(srcdir)
        parent, name = os.path.split(srcdir)
        args = ['tar', '--create', '--xz', '--directory', parent, '--file', targetfilepath, name]
        env = {'XZ_OPT': '-9'}
        run_job(args, env=env, errormsg='Making release archive failed')

    if not codons.release.publish:
        raise CodonsError('Codons for publishing not found. Nothing to do for release')

    project_tag = codons.project.tag

    release_archive_name = derive_archive_name(release_name)
    release_s3_path = derive_s3_path(project_tag, release_name)

    with tempfile.TemporaryDirectory() as tempdir:
        log.debug('Making artifacts...')
        targetroot = os.path.join(tempdir, release_name)
        os.makedirs(targetroot)
        current_dir = pathlib.Path.cwd()
        scmtools.archive(current_dir, targetroot)
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
                raise CodonsError('Invalid local directory for publishing: {}'.format(localdir))
            if not force and os.path.exists(os.path.join(localdir, release_archive_name)):
                raise RibosomeError('Release [{}] already published at local directory [{}]'.format(release_name, localdir))
            shutil.copy(release_archive_path, localdir)
        if codons.release.publish.s3bucket:
            log.debug('Publishing to Amazon S3...')
            s3bucket = codons.release.publish.s3bucket
            s3 = boto3.resource('s3')
            if not force and is_s3_object_exists(s3, s3bucket, release_s3_path):
                raise RibosomeError('Release [{}] already published at Amazon S3 bucket [{}]'.format(release_name, s3bucket))
            s3.Object(s3bucket, release_s3_path).upload_file(release_archive_path)
    log.info('Release [%s] published', release_name)
    report(hooks, 'Release [%s] published', release_name)


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
                    command = args[0]
                    raise RemoteError('Host [{}] failed to run command: {}'.format(host, command))
        return result

    return remote_operation


remote_run = make_remote_operation(fapi.run, logstreams=True, shell=True)
remote_sudo = make_remote_operation(fapi.sudo, logstreams=True, shell=True)
remote_put = make_remote_operation(fapi.put)
remote_get = make_remote_operation(fapi.get)


def is_remote_path_exists(filepath, isdir=False, check_read_permission=False):
    check_symbol = 'd' if isdir else 'f'
    result = remote_run('test -{} {}'.format(check_symbol, filepath))
    if result.failed:
        return False
    if check_read_permission:
        result = remote_run('test -r {}'.format(filepath))
        if result.failed:
            return False
    return True


def split_release_name(release_name):
    project_tag, version = release_name.rsplit('-', 1)
    return project_tag, version


@fapi.task
def upload_release(host, release_name, s3bucket, force=False):
    log.debug('Starting release upload process...')

    release_archive_name = derive_archive_name(release_name)
    project_tag, version = split_release_name(release_name)
    release_s3_path = derive_s3_path(project_tag, release_name)

    s3 = boto3.resource('s3')

    log.debug('Checking artifacts...')

    if not is_s3_object_exists(s3, s3bucket, release_s3_path):
        raise RibosomeError('Release [{}] not found at S3 bucket [{}]'.format(release_name, s3bucket))

    def ensure_dir_exists(dirpath):
        result = remote_run('mkdir -p {}'.format(dirpath))
        if result.failed:
            raise RemoteError('Failed to make directories')

    ensure_dir_exists(constants.RELEASES_REMOTE_ROOT)

    release_archive_name = derive_archive_name(release_name)
    remote_release_archive_path = constants.RELEASES_REMOTE_ROOT.joinpath(release_archive_name)

    release_archive_exists = is_remote_path_exists(remote_release_archive_path, check_read_permission=True)

    if not force and release_archive_exists:
        # TODO: file checksum compare
        log.info('Release [%s] already found uploaded at remote host [%s]: %s', release_name, host, remote_release_archive_path)
        log.warning('No checksum checking was done - trusting the remote file blindly')
    else:
        with tempfile.TemporaryDirectory() as tempdir:
            local_release_archive_path = os.path.join(tempdir, release_archive_name)
            log.debug('Downloading release from Amazon S3...')
            s3.Object(s3bucket, release_s3_path).download_file(local_release_archive_path)
            log.debug('Downloaded to: %s', local_release_archive_path)
            log.debug('Uploading release archive to host [%s]...', host)
            result = remote_put(local_release_archive_path, str(remote_release_archive_path))
            if result.failed:
                raise RemoteError('Failed to upload release archive to remote host')

    remote_project_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag)

    log.debug('Extracting release archive to deploy location...')
    ensure_dir_exists(remote_project_root)
    result = remote_run('tar --extract --directory {} --file {}'.format(remote_project_root, remote_release_archive_path))
    if result.failed:
        raise RemoteError('Failed to extract release archive at remote host')

    log.info('Release uploaded to remote host')


@fapi.task
def setup_runtime_environment(host, release_name, commands):
    if not commands:
        return
    log.debug('Starting runtime environment setup...')
    project_tag, version = split_release_name(release_name)
    remote_release_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    with fapi.cd(str(remote_release_root)):
        for command in commands:
            result = remote_run(command)
            if result.failed:
                raise RemoteError('Failed to setup runtime environment by: {}'.format(command))
    log.info('Runtime environment at remote host configured')


@fapi.task
def update_services_index(host, release_name, service, config, include=None):
    assert include is not None
    project_tag, version = split_release_name(release_name)
    remote_project_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag)
    service_index_path = remote_project_root.joinpath(constants.SERVICE_INDEX_FILENAME)

    service_index_exists = is_remote_path_exists(service_index_path, check_read_permission=True)

    service_index = {}

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
                log.warning('Service index corrupted: %s', e)
                log.warning('Creating new blank service index')
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
        if isinstance(obj, collections.abc.Mapping):
            return {k: make_dict(v) for k, v in obj.items()}
        else:
            return obj

    service_index = make_dict(service_index)

    buf = io.StringIO()
    yaml.dump(dict(services=service_index), buf)
    remote_put(buf, str(service_index_path))


def remote_list_subdirs(host, dirpath):
    # expand user directory [~]
    r = remote_run('echo {}'.format(dirpath))
    projects_abspath = pathlib.PurePosixPath(str(r))
    sftp = fabric.sftp.SFTP(host)
    r = sftp.ftp.listdir(str(projects_abspath))
    subdirs = [name for name in r if sftp.isdir(str(projects_abspath.joinpath(name)))]
    return subdirs


@fapi.task
def get_services_index(host, project_tag):

    if project_tag:
        log.debug('Fetching service index for project [%s] at host [%s]...', project_tag, host)
        project_tags = [project_tag]
    else:
        log.debug('Fetching service indices for all projects at host [%s]...', host)
        project_tags = remote_list_subdirs(host, constants.PROJECTS_REMOTE_ROOT)

    service_indices = {}

    for ptag in project_tags:

        remote_project_root = constants.PROJECTS_REMOTE_ROOT.joinpath(ptag)
        service_index_path = remote_project_root.joinpath(constants.SERVICE_INDEX_FILENAME)

        service_index_exists = is_remote_path_exists(service_index_path, check_read_permission=True)

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
                raise RemoteError('Service index of project [{}] corrupted: {}'.format(ptag, e))
            else:
                service_index = dict(service_index)
                service_indices[ptag] = service_index

    return service_indices


@fapi.task
def get_remote_codons(host, project_tag, release_name):

    remote_release_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    codons_path = remote_release_root.joinpath('codons.yaml')

    codons_exists = is_remote_path_exists(codons_path, check_read_permission=True)
    if not codons_exists:
        raise RemoteError('Codons not found for release [{}] at host [{}]'.format(release_name, host))

    yaml = ryaml.YAML()

    # text stream or StringIO not working here
    with tempfile.TemporaryFile() as stream:
        remote_get(str(codons_path), stream)
        stream.seek(0)
        try:
            codons = yaml.load(stream)
        except Exception as e:
            raise RemoteError('Codons corrupted for release [{}] at host [{}]: {}'.format(release_name, host, e)) from e

    return as_object(codons)


@fapi.task
def get_remote_versions_deployed(host, project_tag):

    if project_tag:
        log.debug('Fetching deployed versions list for project [%s] at host [%s]...', project_tag, host)
        project_tags = [project_tag]
    else:
        log.debug('Fetching deployed versions list for all projects at host [%s]...', host)
        project_tags = remote_list_subdirs(host, constants.PROJECTS_REMOTE_ROOT)

    versions_deployed = collections.defaultdict(set)

    for ptag in project_tags:
        remote_project_root = constants.PROJECTS_REMOTE_ROOT.joinpath(ptag)
        subdirs = remote_list_subdirs(host, remote_project_root)
        release_prefix = ptag + '-'
        for name in subdirs:
            if name.startswith(release_prefix):
                version = name[len(release_prefix):]
                versions_deployed[ptag].add(version)

    return versions_deployed


@fapi.task
def run_service_commands(host, project_tag, release_name, commands, sudo=False):
    if not commands:
        return
    runner = remote_sudo if sudo else remote_run
    remote_release_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)
    with fapi.cd(str(remote_release_root)):
        for command in commands:
            result = runner(command)
            if result.failed:
                raise RemoteError('Failed to run service command: {}'.format(command))


@fapi.task
def load_service(host, project_tag, release_name, service, config, hooks):
    log.debug('Loading service [%s] configuration [%s] from release [%s] at host [%s]: starting...', service, config, release_name, host)
    codons = get_remote_codons(host, project_tag, release_name)
    if not codons.service or not codons.service.load:
        raise CodonsError('Service [load] commands not found')
    load_commands = codons.service.load.get('commands', [])
    load_commands = [cmd.format(service=service, config=config) for cmd in load_commands]
    run_service_commands(host, project_tag, release_name, load_commands, sudo=True)
    update_services_index(host, release_name, service, config, include=True)
    log.info('Loading service [%s] configuration [%s] from release [%s] at host [%s]: done', service, config, release_name, host)
    report(hooks, 'Service [%s] configuration [%s] from release [%s] loaded at host [%s]', service, config, release_name, host)


@fapi.task
def unload_service(host, project_tag, release_name, service, config, hooks):
    log.debug('Unloading service [%s] configuration [%s] from release [%s] at host [%s]: starting...', service, config, release_name, host)
    codons = get_remote_codons(host, project_tag, release_name)
    if not codons.service or not codons.service.unload:
        raise CodonsError('Service [unload] commands not found')
    unload_commands = codons.service.unload.get('commands', [])
    unload_commands = [cmd.format(service=service, config=config) for cmd in unload_commands]
    run_service_commands(host, project_tag, release_name, unload_commands, sudo=True)
    update_services_index(host, release_name, service, config, include=False)
    log.info('Unloading service [%s] configuration [%s] from release [%s] at host [%s]: done', service, config, release_name, host)
    report(hooks, 'Service [%s] configuration [%s] from release [%s] unloaded at host [%s]', service, config, release_name, host)


@fapi.task
def run_service_action(host, project_tag, release_name, service, config, action, args, hooks):
    log.debug('Running action [%s] via service [%s] configuration [%s] from release [%s] at host [%s]: starting...', action, service, config, release_name, host)
    codons = get_remote_codons(host, project_tag, release_name)
    if not codons.service or not codons.service.do:
        raise CodonsError('Service [do] commands not found')
    do_commands = codons.service.do.get('commands', [])
    args_str = ' '.join(args)
    do_commands = [cmd.format(service=service, config=config, action=action, args=args_str) for cmd in do_commands]
    run_service_commands(host, project_tag, release_name, do_commands)
    log.info('Running action [%s] via service [%s] configuration [%s] from release [%s] at host [%s]: done', action, service, config, release_name, host)
    report(hooks, 'Action [%s] completed via service [%s] configuration [%s] from release [%s] at host [%s]', action, service, config, release_name, host)


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


def match_versions(versions, versions_pattern):

    def derive_regexp(pattern):
        prefix = r''
        suffix = r''
        if pattern.startswith(r'*'):
            prefix = r'\S+'
            pattern = pattern[1:]
        regexp = r'^' + prefix + pattern.replace(r'*', r'\S*') + suffix + r'$'
        return re.compile(regexp)

    versions_regexps = [derive_regexp(pattern) for pattern in versions_pattern.split(',')]

    matches = []
    for version in versions:
        if any(pattern.match(version) for pattern in versions_regexps):
            matches.append(version)
    return matches


@fapi.task
def remove_release(host, project_tag, release_name):
    log.debug('Removing release [%s]...', release_name)
    codons = get_remote_codons(host, project_tag, release_name)

    remote_release_root = constants.PROJECTS_REMOTE_ROOT.joinpath(project_tag).joinpath(release_name)

    if codons.cleanup:
        cleanup_commands = codons.cleanup.get('commands', [])
        if cleanup_commands:
            log.debug('Runtime environment cleanup...')
            with fapi.cd(str(remote_release_root)):
                for command in cleanup_commands:
                    result = remote_sudo(command)
                    if result.failed:
                        log.warning('Failed to cleanup runtime environment by: {}'.format(command))

    log.debug('Removing release directory...')
    result = remote_sudo('rm -rf {}'.format(remote_release_root))
    if result.failed:
        raise RemoteError('Failed to remove release directory')

    release_archive_name = derive_archive_name(release_name)
    remote_release_archive_path = constants.RELEASES_REMOTE_ROOT.joinpath(release_archive_name)

    log.debug('Removing release archive...')
    result = remote_run('rm -rf {}'.format(remote_release_archive_path))
    if result.failed:
        raise RemoteError('Failed to remove release archive')


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
    logging.captureWarnings(True)
    logging.getLogger('py.warnings').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    logging.getLogger('boto3').setLevel(logging.WARNING)
    logging.getLogger('s3transfer').setLevel(logging.WARNING)
    logging.getLogger('paramiko').setLevel(logging.WARNING)


def welcome():
    log.info('Ribosome {}. Let\'s make a bit of proteins...'.format(__version__))


def process_errors(f):

    @functools.wraps(f)
    def decorated(*args, **kwargs):
        verbose = True
        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            ctx = ctx.find_root()
            verbose = ctx.params.get('verbose', True)
        try:
            return f(*args, **kwargs)
        except (scmtools.errors.ScmError, scmtools.errors.CommandRunError) as e:
            log.error(e, exc_info=verbose)
        except RibosomeError as e:
            log.error(e, exc_info=verbose)
        except Exception as e:
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
        'verbose': verbose,
        'force': force,
    }
    ctx.obj = as_object(settings)
    setup_logging(verbose=verbose)


@cli.command(short_help='Initialize Ribosome project')
@click.pass_obj
@process_errors
def init(settings):
    """Initialize current directory with codons.yaml"""
    welcome()
    current_dir = pathlib.Path.cwd()
    codons_path = current_dir.joinpath('codons.yaml')
    if codons_path.exists():
        raise RibosomeError('Current directory already contains: codons.yaml')
    project_tag = current_dir.name
    codons_yaml = constants.CODONS_TEMPLATE.format(project_tag=project_tag)
    with io.open(codons_path, 'w', encoding='utf-8') as ostream:
        ostream.write(codons_yaml)
    log.info('Clean codons initialized: codons.yaml')


@cli.group()
def version():
    """Show version or update meta descriptor"""
    pass


@version.command('info')
@process_errors
def version_info():
    """Show project version information"""
    welcome()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    try:
        codons = read_project_codons()
        tag_policy = load_tag_policy(codons)
        is_allowed_for_release = tag_policy(scm_info.tag)
        if not is_allowed_for_release:
            log.warning('Tag is not allowed for release: %s', scm_info.tag)
    except CodonsError:
        pass


@version.command('update')
@process_errors
def version_update():
    """Update project meta descriptor with current version"""
    welcome()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    codons = read_project_codons()
    tag_policy = load_tag_policy(codons)
    is_allowed_for_release = tag_policy(scm_info.tag)
    if not is_allowed_for_release:
        log.warning('Tag is not allowed for release: %s', scm_info.tag)
    if not codons.meta:
        raise CodonsError('No meta descriptor settings defined')
    write_meta(codons, version, scm_info)


@cli.command()
@click.pass_obj
@process_errors
def release(settings):
    """Make release and publish artifacts"""
    welcome()
    scm_info = scm_describe()
    version = derive_version_string(scm_info)
    codons = read_project_codons()
    hooks = load_hooks(codons)
    tag_policy = load_tag_policy(codons)
    is_allowed_for_release = tag_policy(scm_info.tag)
    if not is_allowed_for_release:
        raise RibosomeError('Tag is not allowed for release: {}'.format(scm_info.tag))
    check_scm_status_for_release(scm_info)
    if codons.meta:
        write_meta(codons, version, scm_info)
    if codons.codestyle:
        commands = codons.codestyle.get('commands', [])
        run_commands('Code style check', commands)
    if codons.build:
        commands = codons.build.get('commands', [])
        run_commands('Building project', commands)
    if codons.test:
        commands = codons.test.get('commands', [])
        run_commands('Running tests', commands)
    if codons.release:
        release_name = derive_release_name(codons.project.tag, version)
        publish_release(codons, release_name, hooks, force=settings.force)


@cli.command(short_help='Deploy release artifacts to host')
@click.argument('version')
@click.argument('host')
@click.pass_obj
@process_errors
def deploy(settings, version, host):
    """Deploy release artifacts to host and prepare for run.

    \b
    Args:
        version: project version to deploy
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()
    codons = read_project_codons()
    hooks = load_hooks(codons)

    if not codons.release or not codons.release.publish or not codons.release.publish.s3bucket:
        raise CodonsError('Codons for Amazon S3 not found. Don\'t know how to get artifact for deploying')

    release_name = derive_release_name(codons.project.tag, version)
    s3bucket = codons.release.publish.s3bucket

    execute_as_remote_task(upload_release, host, release_name, s3bucket, force=settings.force)

    # pip install --user pipenv
    # .profile

    remote_codons = execute_as_remote_task(get_remote_codons, host, codons.project.tag, release_name)

    if remote_codons.setup:
        setup_commands = remote_codons.setup.get('commands', [])
        execute_as_remote_task(setup_runtime_environment, host, release_name, setup_commands)

    log.info('Release [%s] deployed at host [%s]', release_name, host)
    report(hooks, 'Release [%s] deployed at host [%s]', release_name, host)


@cli.command(short_help='Load service at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('version')
@click.argument('service', type=str)
@click.argument('config', type=str)
@click.argument('host')
@click.pass_obj
@process_errors
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
    hooks = load_hooks(local_codons)

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    codons = execute_as_remote_task(get_remote_codons, host, project_tag, release_name)
    matches = find_service_configs(codons, service, config)
    log.debug('Service configs matched: %s', matches)
    if not matches:
        log.info('No matched service configs found: nothing to do')
        return

    for service, config in matches:
        log.debug('Processing service [%s] configuration [%s]...', service, config)
        if service in project_service_index and config in project_service_index[service]:
            old_version_loaded = project_service_index[service][config]
            old_service_release_name = derive_release_name(project_tag, old_version_loaded)
            execute_as_remote_task(unload_service, host, project_tag, old_service_release_name, service, config, hooks)
        execute_as_remote_task(load_service, host, project_tag, release_name, service, config)


@cli.command(short_help='Unload service at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('version')
@click.argument('service')
@click.argument('config')
@click.argument('host')
@click.pass_obj
@process_errors
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
    hooks = load_hooks(local_codons)

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    codons = execute_as_remote_task(get_remote_codons, host, project_tag, release_name)
    matches = find_service_configs(codons, service, config)
    log.debug('Service configs matched: %s', matches)
    if not matches:
        log.info('No matched service configs found: nothing to do')
        return None, None

    for service, config in matches:
        log.debug('Processing service [%s] configuration [%s]...', service, config)
        if service in project_service_index and config in project_service_index[service]:
            version_loaded = project_service_index[service][config]
            if version != version_loaded:
                log.warning('Skipping unload service [%s] config [%s]: version loaded [%s] does not match requested [%s]', service, config, version_loaded, version)
                continue
        execute_as_remote_task(unload_service, host, project_tag, release_name, service, config, hooks)


@cli.command(short_help='Reload all services to version at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('version')
@click.argument('host')
@click.pass_obj
@process_errors
def jump(settings, password, version, host):
    """Reload all already loaded services to specific project version at remote host.

    \b
    Args:
        version: project version to use
        host: destination host alias (usage of ssh config assumed)
    """
    setup_global_remote_sudo_password(password)
    welcome()
    local_codons = read_project_codons()
    hooks = load_hooks(local_codons)

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    if not project_service_index:
        log.info('No loaded services found at host [%s]. Nothing to do', host)
        return

    for service, loaded_info in project_service_index.items():
        for config, old_version_loaded in loaded_info.items():
            old_service_release_name = derive_release_name(project_tag, old_version_loaded)
            execute_as_remote_task(unload_service, host, project_tag, old_service_release_name, service, config, hooks)
            execute_as_remote_task(load_service, host, project_tag, release_name, service, config, hooks)


@cli.command(short_help='Run command for service at remote host')
@click.argument('version')
@click.argument('service')
@click.argument('config')
@click.argument('action')
@click.argument('args', nargs=-1)
@click.argument('host')
@click.pass_obj
@process_errors
def do(settings, version, service, config, action, args, host):
    """Run action for service with chosen configuration.

    \b
    Args:
        version: project version to use
        service: service name
        config: configuration name
        action: action to run
        [args]: action command line arguments
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()
    local_codons = read_project_codons()
    hooks = load_hooks(local_codons)

    project_tag = local_codons.project.tag
    release_name = derive_release_name(project_tag, version)

    execute_as_remote_task(run_service_action, host, project_tag, release_name, service, config, action, args, hooks)


@cli.command(short_help='Show loaded services at remote host')
@click.option('-a', '--all', 'search_all_projects', is_flag=True, help='Search through all projects')
@click.argument('host')
@click.pass_obj
@process_errors
def show(settings, search_all_projects, host):
    """Show loaded services at remote host.

    \b
    Args:
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()

    if search_all_projects:
        project_tag = None
        log.info('Inspecting services at host [%s]...', host)
    else:
        codons = read_project_codons()
        project_tag = codons.project.tag
        log.info('Inspecting services of project [%s] at host [%s]...', project_tag, host)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)

    if service_indices is None:
        if search_all_projects:
            log.info('No services indices found')
        else:
            log.info('No services index for project [%s] found', project_tag)
    else:
        if service_indices:
            projects = sorted(list(service_indices.keys()))
            for ptag in projects:
                services_index = service_indices[ptag]
                if services_index:
                    log.info('[%s]: services loaded:', ptag)
                    index = []
                    for service, configs in services_index.items():
                        index.extend((service, config, version) for config, version in configs.items())
                    index = sorted(index)
                    for service, config, version in index:
                        log.info('    %s/%s: %s', service, config, version)
                else:
                    log.info('[%s]: no loaded services found', ptag)
        else:
            log.info('No loaded services found')


@cli.command(short_help='List deployed versions at remote host')
@click.option('-a', '--all', 'search_all_projects', is_flag=True, help='Search through all projects')
@click.argument('host')
@click.pass_obj
@process_errors
def ls(settings, search_all_projects, host):
    """List deployed versions at remote host.

    \b
    Args:
        host: destination host alias (usage of ssh config assumed)
    """
    welcome()

    if search_all_projects:
        project_tag = None
        log.info('Looking for deployed versions at host [%s]...', host)
    else:
        codons = read_project_codons()
        project_tag = codons.project.tag
        log.info('Looking for deployed versions of project [%s] at host [%s]...', project_tag, host)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    if service_indices is None:
        service_indices = {}

    project_versions_with_services_loaded = collections.defaultdict(set)
    for ptag, services_index in service_indices.items():
        if services_index:
            for service, configs in services_index.items():
                for config, version in configs.items():
                    project_versions_with_services_loaded[ptag].add(version)

    versions_deployed = execute_as_remote_task(get_remote_versions_deployed, host, project_tag)

    projects = sorted(list(versions_deployed.keys()))
    for ptag in projects:
        versions = versions_deployed[ptag]
        if versions:
            versions_sorted = sorted(list(versions))
            log.info('[%s]: deployed versions:', ptag)
            versions_loaded = project_versions_with_services_loaded[ptag]
            for v in versions_sorted:
                if v in versions_loaded:
                    log.info('    %s - [loaded]', v)
                else:
                    log.info('    %s', v)
        else:
            log.info('[%s]: no deployed versions found', ptag)

    if not projects:
        log.info('No deployed versions found')


@cli.command(short_help='Uninstall deployed versions at remote host')
@click.option('--password', prompt='[sudo] password for remote host', hide_input=True)
@click.argument('versions')
@click.argument('host')
@click.pass_obj
@process_errors
def gc(settings, password, versions, host):
    """Uninstall deployed versions at remote host.

    \b
    Args:
        versions: versions mask to uninstall
        host: destination host alias (usage of ssh config assumed)
    """
    setup_global_remote_sudo_password(password)
    welcome()

    codons = read_project_codons()
    project_tag = codons.project.tag
    log.info('Looking for deployed and loaded versions of project [%s] at host [%s]...', project_tag, host)

    service_indices = execute_as_remote_task(get_services_index, host, project_tag)
    project_service_index = service_indices.get(project_tag, {}) if service_indices is not None else {}

    versions_loaded = set()
    for service, configs in project_service_index.items():
        for config, version in configs.items():
            versions_loaded.add(version)

    all_versions_deployed = execute_as_remote_task(get_remote_versions_deployed, host, project_tag)
    versions_deployed = all_versions_deployed.get(project_tag, set())

    versions_pattern = versions
    versions_matched = match_versions(versions_deployed, versions_pattern)
    versions_matched = sorted(list(versions_matched))

    log.info('Versions pattern: %s', versions)
    log.info('Versions matched: %s', ', '.join(versions_matched) or 'none')

    versions_removed = []
    versions_skipped = []

    for version in versions_matched:
        if version in versions_loaded:
            versions_skipped.append(version)
            log.info('Skipped: %s (currently loaded)', version)
            continue
        log.debug('Removing: %s', version)
        release_name = derive_release_name(project_tag, version)
        execute_as_remote_task(remove_release, host, project_tag, release_name)
        versions_removed.append(version)
        log.info('Removed: %s', version)

    log.info('Versions skipped: %s', ', '.join(versions_skipped) or 'none')
    log.info('Versions removed: %s', ', '.join(versions_removed) or 'none')


fabric_global_patch()
logging_global_patch()


if __name__ == '__main__':
    cli()
