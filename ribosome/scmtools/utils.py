
import logging
import os
import subprocess

log = logging.getLogger('scmtools.utils')

# https://unix.stackexchange.com/a/270979
# https://www.gnu.org/software/bash/manual/bashref.html#Escape-Character
BASH_ESCAPE_SYMBOLS = r"`~!#$&*(){[|\;'<>? "


def run(args, cwd=None, errormsg=None, check=True):

    cmd = args[0]

    def format_command_token(s):
        for symbol in BASH_ESCAPE_SYMBOLS:
            if symbol in s:
                return '"{}"'.format(s)
        if '"' in s:
            return "'{}'".format(s)
        return s

    def guess_command_line(args):
        return ' '.join(format_command_token(str(arg)) for arg in args)

    log.debug('run: %s', guess_command_line(args))

    no_i18n_env = dict(
        LC_ALL="C",
        LANGUAGE="",
        HGPLAIN="1",
    )

    job_env = os.environ.copy()
    job_env.update(no_i18n_env)

    TIMEOUT = 10  # seconds

    try:
        job = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=job_env,
            timeout=TIMEOUT,
        )
    except OSError as e:
        if errormsg:
            msg = '{}: {}'.format(errormsg, e)
        else:
            msg = 'Failed to run [{}]: {}'.format(cmd, e)
        return None, msg
    else:
        if check and job.returncode != 0:
            return None, 'Failed to run [{}], return code: {}'.format(cmd, job.returncode)
        output = job.stdout.decode('utf-8', 'surrogateescape').strip()
        return output, None
