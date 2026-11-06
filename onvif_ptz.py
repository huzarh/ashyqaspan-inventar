"""Minimal ONVIF PTZ client (stdlib only) for pan/tilt cameras such as the Tapo C210.

Uses WS-Security UsernameToken digest auth; Tapo serves every ONVIF service on http://<ip>:2020/onvif/service.
"""
import base64
import hashlib
import os
import re
import urllib.request
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

DIRECTIONS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, 1), 'down': (0, -1)}


class OnvifError(Exception):
    pass


class PTZCamera:
    def __init__(self, url):
        """url: http://user:pass@host:2020 (user/pass may be percent-encoded)."""
        u = urlparse(url)
        self.user, self.password = unquote(u.username or ''), unquote(u.password or '')
        self.endpoint = '%s://%s:%s/onvif/service' % (u.scheme or 'http', u.hostname, u.port or 2020)
        self._profile = None

    def _security(self):
        nonce = os.urandom(16)
        created = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return ('<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" '
                'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"><wsse:UsernameToken>'
                '<wsse:Username>%s</wsse:Username>'
                '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">%s</wsse:Password>'
                '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-soap-message-security-1.0#Base64Binary">%s</wsse:Nonce>'
                '<wsu:Created>%s</wsu:Created></wsse:UsernameToken></wsse:Security>'
                % (_xml(self.user), digest, base64.b64encode(nonce).decode(), created))

    def _call(self, body, timeout=8):
        envelope = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" '
                    'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Header>%s</s:Header><s:Body>%s</s:Body></s:Envelope>'
                    % (self._security(), body))
        req = urllib.request.Request(self.endpoint, data=envelope.encode(), headers={'Content-Type': 'application/soap+xml; charset=utf-8'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            raise OnvifError('ONVIF %s: %s' % (e.code, _fault(e.read().decode('utf-8', 'replace'))))
        except OSError as e:
            raise OnvifError('ONVIF bağlantı hatası: %s' % e)

    def profile(self):
        if not self._profile:
            xml = self._call('<trt:GetProfiles/>')
            m = re.search(r'<[^>]*Profiles[^>]*token="([^"]+)"', xml)
            if not m:
                raise OnvifError('ONVIF profili bulunamadı.')
            self._profile = m.group(1)
        return self._profile

    def move(self, direction, step=0.04):
        """Fixed-size step. RelativeMove needs no Stop, so a slow Wi-Fi link cannot leave the camera spinning."""
        if direction not in DIRECTIONS:
            raise OnvifError('Geçersiz yön.')
        x, y = DIRECTIONS[direction]
        self._call('<tptz:RelativeMove><tptz:ProfileToken>%s</tptz:ProfileToken><tptz:Translation>'
                   '<tt:PanTilt x="%.3f" y="%.3f" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/TranslationGenericSpace"/>'
                   '</tptz:Translation></tptz:RelativeMove>' % (_xml(self.profile()), x*step, y*step), timeout=15)


def _xml(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def _fault(xml):
    m = re.search(r'<[^>]*Text[^>]*>([^<]+)<', xml)
    return m.group(1) if m else xml[:200]
