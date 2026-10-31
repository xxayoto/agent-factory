"""Сеть на macOS: python.org-сборки Python не видят сертификаты связки ключей (VPN/прокси с своим корнем
дают CERTIFICATE_VERIFY_FAILED). Собираем бандл из Keychain один раз и отдаём его OpenSSL через SSL_CERT_FILE."""
import os
import subprocess
import sys

CACHE = os.path.expanduser('~/.cache/agent-factory/keychain.pem')
KEYCHAINS = ['/System/Library/Keychains/SystemRootCertificates.keychain', '/Library/Keychains/System.keychain',
             os.path.expanduser('~/Library/Keychains/login.keychain-db')]


def use_macos_keychain():
    if sys.platform != 'darwin' or os.environ.get('SSL_CERT_FILE'):
        return
    if not os.path.exists(CACHE):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        pem = subprocess.run(['security', 'find-certificate', '-a', '-p', *[k for k in KEYCHAINS if os.path.exists(k)]],
                             capture_output=True, text=True).stdout
        if 'BEGIN CERTIFICATE' not in pem:
            return
        with open(CACHE, 'w') as f:
            f.write(pem)
    os.environ['SSL_CERT_FILE'] = CACHE
