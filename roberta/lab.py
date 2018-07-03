import ctypes
from fcntl import ioctl
import json
import logging
import os
import socket
import stat
import struct
import time
import _thread
import threading
import urllib.request
import urllib.error
import urllib.parse
import sys

# logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('roberta.lab')

TOKEN_PER_SESSION = True

# helpers
def getHwAddr(ifname):
    # SIOCGIFHWADDR = 0x8927
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        info = ioctl(s.fileno(), 0x8927, struct.pack('256s', ifname[:15]))
    return ':'.join(['%02x' % char for char in info[18:24]])


def generateToken():
    # note: we intentionally leave '01' and 'IO' out since they can be confused
    # when entering the code
    chars = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
    # note: we don't use the random module since it is large
    b = os.urandom(8)
    return ''.join(chars[b[i] % len(chars)] for i in range(8))


class Connector(threading.Thread):
    """OpenRobertab-Lab network IO thread"""

    def __init__(self, address):
        threading.Thread.__init__(self)
        self.address = address
        self.home = os.path.expanduser("~")
        self.params = {}

        # or /etc/os-release
        with open('/proc/version', 'r') as ver:
            self.params['firmwareversion'] = ver.read()

        for iface in ['wlan', 'usb', 'eth']:
            for ix in range(10):
                try:
                    ifname = bytes(iface + str(ix), 'ascii')
                    self.params['macaddr'] = getHwAddr(ifname)
                    break
                except IOError:
                    pass

        if TOKEN_PER_SESSION:
            self.params['token'] = generateToken()
            print("Please enter the following token to the Web frontent: " + self.params['token'])

        self.registered = False
        self.running = True
        logger.debug('thread created')

    def _store_code(self, filename, code):
        # TODO: what can we do if the file can't be overwritten
        # https://github.com/OpenRoberta/robertalab-ev3dev/issues/26
        # - there is no point in catching if we only log it
        # - once we can report error details to the server, we can reconsider
        #   https://github.com/OpenRoberta/robertalab-ev3dev/issues/20
        with open(filename, 'w') as prog:
            # Apply hotfixes needed until server update
            # - the server generated code is python2 still
            code = code.replace('from __future__ import absolute_import\n', '')
            code = code.replace('in xrange(', 'in range(')
            code = code.replace('#!/usr/bin/python\n', '#!/usr/bin/python3\n')
            prog.write(code)
        os.chmod(filename, stat.S_IXUSR | stat.S_IRUSR | stat.S_IWUSR)
        return code

    def _exec_code(self, filename, code):
        result = 0
        # using a new process would be using this, but is slower (4s vs <1s):
        # result = subprocess.call(["python", filename], env={"PYTHONPATH":"$PYTONPATH:."})
        # logger.info('execution result: %d' % result)
        #
        # NOTE: we don't have to keep pinging the server while running
        #   the code - robot is busy until we send push request again
        #   it would be nice though if we could cancel the running program
        try:
            compiled_code = compile(code, filename, 'exec')
            scope = {
                '__name__': '__main__',
                'result': 0,
            }
            exec(compiled_code, scope)
            result = scope['result']
            logger.info('execution finished: result = %d', result)
        except KeyboardInterrupt:
            logger.info("reraise hard kill")
            raise
        except SystemExit:
            result = 143
            logger.info("soft kill")
        except:  # noqa: E722
            result = 1
            # TODO: return exception details as a string and put into a
            # 'nepoexitdetails' field, so that we can show this in the UI
            logger.exception("Ooops:")
        return result

    def _request(self, cmd, headers, timeout):
        url = '%s/%s' % (self.address, cmd)
        while True:
            try:
                logger.debug('sending request to: %s, timeout %d', url, timeout)
                req = urllib.request.Request(url, headers=headers)
                data = json.dumps(self.params).encode('utf8')
                logger.debug('  with params: %s', data)
                return urllib.request.urlopen(req, data, timeout=timeout)
            except urllib.error.HTTPError as e:
                if e.code == 404 and '/rest/' not in url:
                    logger.warning("HTTPError(%s): %s, retrying with '/rest'", e.code, e.reason)
                    # upstream changed the server path
                    url = '%s/rest/%s' % (self.address, cmd)
                elif e.code == 405 and not url.startswith('https://'):
                    logger.warning("HTTPError(%s): %s, retrying with 'https://'", e.code, e.reason)
                    self.address = "https" + self.address[4:]
                    url = "https" + url[4:]
                else:
                    raise e
        return None

    def run(self):
        logger.debug('network thread started')
        # network related locals
        # TODO: change the user agent:
        # https://docs.python.org/2/library/urllib2.html#urllib2.Request
        # default is "Python-urllib/2.7"
        headers = {
            'Content-Type': 'application/json'
        }
        timeout = 15  # seconds

        logger.debug('target: %s', self.address)
        while self.running:
            if self.registered:
                self.params['cmd'] = 'push'
                timeout = 15
            else:
                self.params['cmd'] = 'register'
                timeout = 330
            # self.params['brickname'] = 'ev3lejosv1' # socket.gethostname()
            self.params['battery'] = 0  # getBatteryVoltage()
            self.params['robot'] = 'turtlebot'

            self.params['firmwareversion'] = ''
            self.params['firmwarename'] = 'ev3dev'
            try:
                # TODO: what about /api/v1/pushcmd
                # TODO: according to https://tools.ietf.org/html/rfc6202
                # we should use keep alive
                # http://stackoverflow.com/questions/1037406/python-urllib2-with-keep-alive
                # http://stackoverflow.com/questions/13881196/remove-http-connection-header-python-urllib2
                # https://github.com/jcgregorio/httplib2
                response = self._request("pushcmd", headers, timeout)
                reply = json.loads(response.read().decode('utf8'))
                logger.debug('response: %s', json.dumps(reply))
                cmd = reply['cmd']
                print("Command received: %s" % cmd)
                if cmd == 'repeat':
                    # if not self.registered:
                    #    self.service.status('registered')
                    #    self.service.hal.playFile(2)
                    self.registered = True
                    self.params['nepoexitvalue'] = 0
                elif cmd == 'abort':
                    # if service is None, the user canceled
                    if not self.registered and self.service:
                        logger.info('token collision, retrying')
                        self.params['token'] = generateToken()
                        # make sure we don't DOS the server
                        time.sleep(1.0)
                    else:
                        break
                elif cmd == 'download':
                    # TODO: url is not part of reply :/
                    # TODO: we should receive a digest for the download (md5sum) so that
                    #   we can verify the download
                    logger.debug('download code: %s/download', self.address)
                    response = self._request('download', headers, timeout)
                    hdr = response.getheader('Content-Disposition')
                    # save to $HOME/
                    filename = os.path.join(self.home, hdr.split('=')[1] if hdr else 'unknown')
                    code = self._store_code(filename, response.read().decode('utf-8'))
                    logger.info('code downloaded to: %s', filename)
                    # This will make brickman switch vt
                    self.params['nepoexitvalue'] = self._exec_code(filename, code)
                elif cmd == 'update':
                    # FIXME: implement
                    # ensure local module dir
                    # os.mkdirs('os.path.expanduser('~/.local/python/roberta/'))
                    # fetch ev3.py and store to ~/.local/python/roberta/
                    # then restart:
                    # os.execv(__file__, sys.argv)
                    # check if we need to close files (logger?)
                    pass
                else:
                    logger.warning('unhandled command: %s', cmd)
            except urllib.error.HTTPError as e:
                # e.g. [Errno 404]
                logger.error("HTTPError(%s): %s", e.code, e.reason)
                break
            except urllib.error.URLError as e:
                # e.g. [Errno 111] Connection refused
                #                  The handshake operation timed out
                # errors can be nested
                nested_e = None
                if len(e.args) > 0:
                    nested_e = e.args[0]
                elif e.__cause__:
                    nested_e = e.__cause__
                retry = False
                if nested_e:
                    # this happens if packets were lost
                    if isinstance(nested_e, socket.timeout):
                        retry = True
                    # this happens if we loose network
                    if isinstance(nested_e, socket.gaierror):
                        retry = True
                    if isinstance(nested_e, socket.herror):
                        retry = True
                    if isinstance(nested_e, socket.error):
                        retry = True
                else:
                    retry = True

                if not retry:
                    logger.error("URLError: %s: %s", self.address, e.reason)
                    logger.debug("URLError: %s", repr(e))
                    if nested_e:
                        logger.debug("Nested Exception: %s", repr(nested_e))
                    break
            except (socket.timeout, socket.gaierror, socket.herror, socket.error):
                pass
            except:  # noqa: E722
                logger.exception("Ooops:")
        logger.info('network thread stopped')
