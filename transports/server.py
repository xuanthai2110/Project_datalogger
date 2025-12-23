import requests
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from cryptography.fernet import Fernet

from config.cfg_project import DATA_DIR, SERVER_URL

TOKEN_FILE = DATA_DIR / "token.bin"
KEY_FILE = DATA_DIR / "token.key"


class APIClientError(Exception):
    """Lỗi chung khi gọi API server."""
    pass


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    token_type: str


class ServerClient:
    def __init__(self, server_url: str = SERVER_URL, token_file: Path | str = TOKEN_FILE, key_file: Path | str = KEY_FILE):
        self.server_url = server_url.rstrip("/")
        self.session = requests.Session()
        self.token_file = Path(token_file)
        self.key_file = Path(key_file)
        self.token: TokenData | None = None
        self._fernet = self._get_fernet()

    # ---------- token encryption / io ----------
    def _get_fernet(self) -> Fernet:
        try:
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            if self.key_file.exists():
                key = self.key_file.read_bytes()
            else:
                key = Fernet.generate_key()
                self.key_file.write_bytes(key)
        except Exception as e:
            raise APIClientError(f"Lỗi khi xử lý key mã hóa: {e}") from e
        return Fernet(key)

    def _save_token(self, token: TokenData) -> None:
        data = asdict(token)
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            plain = json.dumps(data).encode("utf-8")
            encrypted = self._fernet.encrypt(plain)
            self.token_file.write_bytes(encrypted)
        except Exception as e:
            print(f"Không lưu được token ra file: {e}")

    def _load_token_dict(self) -> dict:
        if not self.token_file.exists():
            raise APIClientError(f"Không tìm thấy file token: {self.token_file}")
        try:
            encrypted = self.token_file.read_bytes()
            plain = self._fernet.decrypt(encrypted)
            data = json.loads(plain.decode("utf-8"))
        except Exception as e:
            raise APIClientError(f"Không đọc/giải mã được file token: {e}") from e
        if not isinstance(data, dict):
            raise APIClientError("File token sau khi giải mã không trả về dict hợp lệ.")
        return data

    def _load_refresh_token(self) -> str:
        data = self._load_token_dict()
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise APIClientError("Trong token (sau khi giải mã) không có refresh_token")
        return refresh_token

    def _load_access_token(self) -> str:
        data = self._load_token_dict()
        access_token = data.get("access_token")
        if not access_token:
            raise APIClientError("Trong token (sau khi giải mã) không có access_token")
        return access_token

    # ---------- HTTP helper with auto-refresh on 401 ----------
    def _auth_headers(self, access_token: str | None = None, content_type: str | None = None) -> dict:
        if access_token is None:
            access_token = self._load_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def request(self, method: str, path: str, *, params: dict | None = None, json_body: dict | None = None,
                data: dict | None = None, access_token: str | None = None, timeout: int = 10, _retry: bool = False):
        """
        Gọi HTTP request, parse JSON hoặc text.
        Nếu server trả 401 và _retry == False thì sẽ cố refresh_token() và retry 1 lần.
        """
        url = f"{self.server_url}/{path.lstrip('/')}"
        headers = self._auth_headers(access_token=access_token, content_type="application/json" if (json_body is not None or data is not None) else None)

        try:
            resp = self.session.request(method, url, headers=headers, params=params, json=json_body, data=data, timeout=timeout)
        except requests.RequestException as e:
            raise APIClientError(f"Lỗi kết nối tới server khi {method} {url}: {e}") from e

        # Parse JSON nếu có
        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        # Nếu 401 và chưa retry, cố refresh token rồi retry một lần
        if resp.status_code == 401 and not _retry:
            # Thử refresh token; nếu refresh lỗi -> ném luôn
            try:
                self.refresh_token()
            except APIClientError as e:
                raise APIClientError(f"401 received and refresh_token failed: {e}") from e
            # Sau khi refresh thành công, thử gọi lại (lúc này _retry=True để tránh loop)
            return self.request(method, path, params=params, json_body=json_body, data=data, access_token=None, timeout=timeout, _retry=True)

        if resp.status_code < 200 or resp.status_code >= 300:
            raise APIClientError(f"{method} {url} failed ({resp.status_code}): {body}")

        return body

    def get(self, path: str, params: dict | None = None, access_token: str | None = None):
        return self.request("GET", path, params=params, access_token=access_token)

    def post(self, path: str, json_body: dict | None = None, access_token: str | None = None):
        return self.request("POST", path, json_body=json_body, access_token=access_token)

    #def patch(self, path: str, json_body: dict | None = None, access_token: str | None = None):
        #return self.request("PATCH",path, json_body=json_body, access_token=access_token):
    
    # ---------- Auth: login / refresh ----------
    def login(
        self,
        username: str,
        password: str,
        scope: str = "",
        client_id: str = "",
        client_secret: str = "",
    ) -> TokenData:
        url_path = "/api/auth/token"
        data = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "scope": scope,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            resp = self.session.post(f"{self.server_url}{url_path}", data=data, headers=headers, timeout=10)
        except requests.RequestException as e:
            raise APIClientError(f"Lỗi kết nối tới server: {e}") from e

        try:
            body = resp.json()
        except ValueError:
            raise APIClientError(f"Server trả về không phải JSON (status {resp.status_code}): {resp.text}")

        if resp.status_code != 200:
            if isinstance(body, dict) and "detail" in body:
                details = body.get("detail") or []
                messages: list[str] = []
                for d in details:
                    loc = d.get("loc")
                    msg = d.get("msg")
                    typ = d.get("type")
                    messages.append(f"{loc} - {typ}: {msg}")
                msg_joined = " ; ".join(messages) if messages else str(body)
                raise APIClientError(f"Login failed ({resp.status_code}): {msg_joined}")
            else:
                raise APIClientError(f"Login failed ({resp.status_code}): {body}")

        try:
            token = TokenData(
                access_token=body["access_token"],
                refresh_token=body["refresh_token"],
                token_type=body.get("token_type", "Bearer"),
            )
        except KeyError as e:
            raise APIClientError(f"Thiếu field trong token response: {e}, body={body}")

        self.token = token
        self.session.headers.update({"Authorization": f"{token.token_type} {token.access_token}"})
        self._save_token(token)
        return token

    def refresh_token(self) -> TokenData:
        """
        Gọi /api/auth/refresh để lấy token mới. Nếu thành công, lưu mã hoá và cập nhật header.
        """
        refresh_token = self._load_refresh_token()
        url_path = "/api/auth/refresh"
        payload = {"refresh_token": refresh_token}

        try:
            resp = self.session.post(f"{self.server_url}{url_path}", json=payload, headers={"Content-Type": "application/json"}, timeout=10)
        except requests.RequestException as e:
            raise APIClientError(f"Lỗi kết nối tới server khi refresh token: {e}") from e

        try:
            body = resp.json()
        except ValueError:
            raise APIClientError(f"Server trả về không phải JSON (status {resp.status_code}): {resp.text}")

        if resp.status_code != 200:
            if isinstance(body, dict) and "detail" in body:
                details = body.get("detail") or []
                messages: list[str] = []
                for d in details:
                    loc = d.get("loc")
                    msg = d.get("msg")
                    typ = d.get("type")
                    messages.append(f"{loc} - {typ}: {msg}")
                msg_joined = " ; ".join(messages) if messages else str(body)
                raise APIClientError(f"Refresh token failed ({resp.status_code}): {msg_joined}")
            else:
                raise APIClientError(f"Refresh token failed ({resp.status_code}): {body}")

        try:
            new_token = TokenData(
                access_token=body["access_token"],
                refresh_token=body["refresh_token"],
                token_type=body.get("token_type", "Bearer"),
            )
        except KeyError as e:
            raise APIClientError(f"Thiếu field trong refresh response: {e}, body={body}")

        self.token = new_token
        self._save_token(new_token)
        self.session.headers.update({"Authorization": f"{new_token.token_type} {new_token.access_token}"})
        return new_token

    # ---------- API-specific helpers ----------
    def get_projectID(self) -> int:
        body = self.get("/api/projects/")
        if not isinstance(body, dict) or "data" not in body:
            raise APIClientError("Response /api/projects không đúng định dạng (thiếu 'data').")
        data_list = body.get("data") or []
        if not data_list:
            raise APIClientError("Danh sách 'data' rỗng, không có project nào.")
        first_item = data_list[0]
        project = first_item.get("project") or {}
        project_id = project.get("id")
        if project_id is None:
            raise APIClientError("Không tìm thấy 'id' trong phần 'project' của response.")
        return project_id

    def get_inverterID(self) -> list[dict]
        project_id = self.get_projectID()
        path = f"/api/projects/{project_id}/inverters/"

        # Dùng params để requests tự đóng gói query string
        body = self.get(path, params={"telemetry": "false"})

        # Nếu server trả {"data": [...]}, unwrap data; nếu trả trực tiếp list thì trả list
        if isinstance(body, dict) and "data" in body:
            data = body.get("data") or []
            if not isinstance(data, list):
                raise APIClientError("Response 'data' không phải list.")
            return data

        if isinstance(body, list):
            return body
        raise APIClientError(f"Unexpected response for {path} with telemetry=false: {body!r}")


    def post_telemetry(self, data: dict, project_id: int) -> dict:
        return self.post(f"/api/telemetry/project/{project_id}", json_body=data)

    def post_telemetry_with_stored_token(self, data: dict, project_id: int) -> dict:
        return self.post_telemetry(data=data, project_id=project_id)

    def post_profile_inverter(self, data: dict) -> dict:
        return self.post("/api/inverters", json_body=data)

    def post_profile_project(self, data: dict) -> dict:
        return self.post("/api/projects", json_body=data)
    # change Inverter
    def patch_inverter(self, inverter_id: int, json_body: dict):

        return self.patch(f"/api/inverters/{inverter_id}", json_body=json_body)