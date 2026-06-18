import aiohttp
import base64

class TGSClient:
    def __init__(self, config):
        self.base_url = str(config["tgs"]["url"]).rstrip('/')
        self.username = config["tgs"].get("user")
        self.password = config["tgs"].get("password")
        self.session_token = None
        print(f"DEBUG: TGSClient инициализирован.")

    async def login(self):
        if self.session_token:
            return self.session_token

        credentials = f"{self.username}:{self.password}"
        encoded_creds = base64.b64encode(credentials.encode()).decode()

        url = f"{self.base_url}/api"
        
        headers = {
            "Authorization": f"Basic {encoded_creds}",
            "Api": "Tgstation.Server.Api/10.14.1", 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json={}) as response:
                if response.status == 200:
                    data = await response.json()
                    self.session_token = data.get("bearer")
                    print("DEBUG: Авторизация успешная.")
                    return self.session_token
                else:
                    print(f"DEBUG: Ошибка логина (POST /api): {response.status}")
                    print(f"DEBUG: Ответ сервера: {await response.text()}")
                    return None

    async def deploy(self, instance_id):
        if not self.session_token:
            await self.login()
        
        url = f"{self.base_url}/api/DreamMaker"
        headers = {
            "Authorization": f"Bearer {self.session_token}",
            "Instance": str(instance_id),
            "Api": "Tgstation.Server.Api/10.14.1",
            "Webpanel-Version": "v6.11.3",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json={}) as response:
                if response.status == 401:
                    print("DEBUG: Токен устарел, запрашиваю новый..")
                    await self.login()
                    return await self.deploy(instance_id)
                
                return response.status == 202
            