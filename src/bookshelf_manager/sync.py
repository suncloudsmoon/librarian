import base64
import json
import os
import socket
import struct
import unicodedata
from hashlib import sha256
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class SocketClosedError(RuntimeError):
    pass


class PasswordMismatchError(RuntimeError):
    pass


class SocketCommunication:

    def __init__(self, password: str):
        self.password = unicodedata.normalize("NFC", password).encode()
        self.f = None

    def setup_key(self, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(), length=32, salt=salt, iterations=1_200_000
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.password))
        self.f = Fernet(key)
        return key

    def encrypt(self, data: bytes) -> bytes:
        return self.f.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self.f.decrypt(data)

    def server_handshake(self, clientsocket: socket.socket):
        salt = os.urandom(16)
        self.send_bytes(clientsocket, salt, encrypt=False)
        key = self.setup_key(salt)

        self.send_info(clientsocket, {"hash": sha256(key).hexdigest()}, encrypt=False)

    def client_handshake(self, clientsocket: socket.socket):
        salt = self.receive_bytes(clientsocket, decrypt=False)
        key = self.setup_key(salt)

        hash = self.receive_info(clientsocket, decrypt=False)["hash"]
        if hash != sha256(key).hexdigest():
            raise PasswordMismatchError()

    def send_num_bytes(self, clientsocket: socket.socket, amount: int):
        if amount < 0 or amount > 2**64:
            raise ValueError(f"invalid amount {amount}")

        data = struct.pack(">Q", amount)
        clientsocket.sendall(data)

    def send_bytes(
        self, clientsocket: socket.socket, data: bytes, encrypt: bool = True
    ):
        data = self.encrypt(data) if encrypt else data
        size = len(data)
        self.send_num_bytes(clientsocket, size)
        data = struct.pack(f">{size}s", data)
        clientsocket.sendall(data)

    def send_info(
        self, clientsocket: socket.socket, packet_info: dict, encrypt: bool = True
    ):
        data = json.dumps(packet_info).encode()
        self.send_bytes(clientsocket, data, encrypt)

    def send_file(self, clientsocket: socket.socket, filepath: Path):
        file = filepath.read_bytes()
        self.send_info(
            clientsocket,
            {
                "type": "file",
                "path": str(filepath),
            },
        )
        self.send_bytes(clientsocket, file)

    def receive_num_bytes(
        self,
        clientsocket: socket.socket,
    ) -> int:
        data = bytearray()
        while (datalen := len(data)) < 8:
            packets = clientsocket.recv(min(8 - datalen, 8))
            if packets == b"":
                raise SocketClosedError()
            data.extend(packets)
        converted_data = struct.unpack(">Q", data)[0]
        return int(converted_data)

    def receive_bytes(self, clientsocket: socket.socket, decrypt: bool = True):
        msglen = self.receive_num_bytes(clientsocket)

        all_data = bytearray()
        while (datalen := len(all_data)) < msglen:
            data = clientsocket.recv(min(msglen - datalen, 2**20))
            if data == b"":
                raise SocketClosedError()
            all_data.extend(data)

        converted_data = struct.unpack(f">{msglen}s", all_data)[0]
        decrypted_data = self.decrypt(converted_data) if decrypt else converted_data
        return decrypted_data

    def receive_info(self, clientsocket: socket.socket, decrypt: bool = True) -> dict:
        data = self.receive_bytes(clientsocket, decrypt)
        info = data.decode()
        return json.loads(info)

    def receive_file(self, clientsocket: socket.socket, directory: Path):
        info = self.receive_info(clientsocket)
        if info["type"] == "file":
            path = Path(info["path"])
            file = self.receive_bytes(clientsocket)
            path.write_bytes(file)
        else:
            raise TypeError(f"unknown type {info["type"]}")


class SyncServer(SocketCommunication):

    def __init__(
        self, password: str, server_address: tuple = (socket.gethostname(), 1230)
    ):
        super().__init__(password)
        self.server_socket = socket.socket(
            family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        print(f"Server binding to {server_address}")
        self.server_socket.bind(server_address)
        self.server_socket.listen(1)

    def start(self, directory: Path):
        (clientsocket, port) = self.server_socket.accept()
        self.server_handshake(clientsocket)

        directory.mkdir(exist_ok=True)
        os.chdir(directory)
        root = Path(".")
        try:
            info = self.receive_info(clientsocket)
            filepaths = [path for path in root.rglob("*") if path.is_file()]
            if info["strict"]:
                self.send_info(
                    clientsocket, {"paths": [str(path) for path in filepaths]}
                )

            for path in filepaths:
                file = path.read_bytes()
                hash = sha256(file).hexdigest()
                self.send_info(clientsocket, {"path": str(path), "hash": hash})
                info = self.receive_info(clientsocket)
                if info["wanted"]:
                    self.send_file(clientsocket, path)
        except KeyboardInterrupt:
            pass
        finally:
            clientsocket.shutdown(socket.SHUT_RDWR)
            clientsocket.close()
        self.server_socket.close()


class SyncClient(SocketCommunication):

    def __init__(self, password: str):
        super().__init__(password)
        self.client_socket = socket.socket(
            family=socket.AF_INET, type=socket.SOCK_STREAM
        )

    def get_file(self, filepath: Path):
        self.send_info(self.client_socket, {"wanted": True})
        filepath.parent.mkdir(parents=True, exist_ok=True)
        self.receive_file(self.client_socket, filepath)
        print(f"Synced {filepath}")

    def refuse_file(self):
        self.send_info(self.client_socket, {"wanted": False})

    def start(
        self,
        directory: Path,
        server_addr: tuple = (socket.gethostname(), 1230),
        exclude_paths: list = [],
        strict: bool = False,
    ):
        directory.mkdir(exist_ok=True)
        os.chdir(directory)
        root = Path(".")

        self.client_socket.connect(server_addr)
        self.client_handshake(self.client_socket)

        self.send_info(self.client_socket, {"strict": strict})
        if strict:
            path_info = self.receive_info(self.client_socket)
            paths = [Path(path) for path in path_info["paths"]]

            # Delete files
            pathiter = root.rglob("*")
            for path in pathiter:
                if path.is_file() and path not in paths and path not in exclude_paths:
                    path.unlink()

            # Delete empty directories
            for path in pathiter:
                if path.exists() and path.is_dir() and path not in exclude_paths:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
        try:
            while True:
                info = self.receive_info(self.client_socket)
                filepath = Path(info["path"])
                if filepath not in exclude_paths:
                    if not filepath.exists():
                        self.get_file(filepath)
                    else:
                        hash = sha256(filepath.read_bytes()).hexdigest()
                        if info["hash"] != hash:
                            self.get_file(filepath)
                        else:
                            self.refuse_file()
                else:
                    self.refuse_file()
        except SocketClosedError:
            pass
        finally:
            self.client_socket.shutdown(socket.SHUT_RDWR)
            self.client_socket.close()
