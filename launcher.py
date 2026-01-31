import requests
import time
import subprocess
import os
import logging
import random
import uuid
from urllib.parse import quote
import mutex_bypass

# Roblox Launcher Strategy
class RobloxLauncher:
    def __init__(self):
        self.session = requests.Session()
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.session.headers.update({"User-Agent": self.user_agent})

    def get_csrf_token(self, cookie):
        """
        Roblox API requires an X-CSRF-TOKEN.
        We request the auth ticket endpoint without a token first to trigger the 403 challenge
        and get the token from the headers.
        """
        try:
            url = "https://auth.roblox.com/v1/authentication-ticket"
            headers = {
                "Cookie": f".ROBLOSECURITY={cookie}",
                "Content-Type": "application/json"
            }
            # This is expected to fail with 403, but return the token
            # We must send strict JSON content type even though we expect failure
            response = self.session.post(url, headers=headers, json={})
            
            if "x-csrf-token" in response.headers:
                return response.headers["x-csrf-token"]
            
            return None
        except Exception as e:
            logging.error(f"Error getting CSRF: {e}")
            return None

    def get_auth_ticket(self, cookie):
        """
        Generates an authentication ticket using the cookie.
        Returns: (ticket, error_message)
        """
        csrf = self.get_csrf_token(cookie)
        if not csrf:
            return None, "Failed to fetch CSRF Token. Check if cookie is valid."

        url = "https://auth.roblox.com/v1/authentication-ticket"
        headers = {
            "Cookie": f".ROBLOSECURITY={cookie}",
            "X-CSRF-TOKEN": csrf,
            "Referer": "https://www.roblox.com/",
            "Origin": "https://www.roblox.com",
            "Content-Type": "application/json"
        }
        
        try:
            response = self.session.post(url, headers=headers, json={})
            if response.status_code == 200:
                ticket = response.headers.get("rbx-authentication-ticket")
                if not ticket:
                     return None, "Response 200 but missing rbx-authentication-ticket header."
                return ticket, None
            else:
                logging.error(f"Failed to get Auth Ticket: {response.status_code} - {response.text}")
                return None, f"API Error {response.status_code}: {response.text[:200]}"
        except Exception as e:
            logging.error(f"Exception getting Auth Ticket: {e}")
            return None, f"Network Exception: {str(e)}"

    def launch_game(self, cookie, place_id, vip_url=None, multi_instance=False):
        """
        Launches Roblox using a HYBRID strategy:
        1. 'roblox-player:...launchmode:app...' -> Opens Roblox App and logs the specific user in.
        2. Wait for login.
        3. 'roblox://...' -> Triggers the game join using the now-active session.
        This combines Multi-Account support (Step 1) with reliable Game Joining (Step 3).
        
        Args:
            multi_instance (bool): If True, attempts to close existing ROBLOX_singletonMutex handles.
        """
        # 1. Handle Multi-Instance / Clean Start
        if multi_instance:
            logging.info("Multi-Instance Mode: Starting background mutex killer...")
            try:
                mutex_bypass.start_mutex_killer()
            except Exception as e:
                logging.error(f"Mutex bypass failed: {e}")
        else:
            # Traditional behavior if needed
            pass

        # 2. Get Authentication Ticket
        ticket, error_msg = self.get_auth_ticket(cookie)
        if not ticket:
            if multi_instance:
                try:
                    mutex_bypass.stop_mutex_killer()
                except: pass
            return False, f"Auth Error: {error_msg}"

        clean_place_id = str(place_id).strip()
        launch_time = str(int(time.time() * 1000))
        browser_tracker_id = str(int(time.time() * 1000) + random.randint(100, 999))
        
        # 3. Step A: Authenticate (Open App Only)
        # We use launchmode:app to just open the client and perform the login handshake.
        auth_args = (
            f"1"
            f"+launchmode:app"
            f"+gameinfo:{ticket}"
            f"+launchtime:{launch_time}"
            f"+browsertrackerid:{browser_tracker_id}"
            f"+robloxLocale:en_us"
            f"+gameLocale:en_us"
        )
        auth_url = f"roblox-player:{auth_args}"
        
        logging.info("Step 1: Authenticating User (Opening App)...")
        self._run_command(auth_url)
        
        # Wait for App to open and login - mutex killer runs in background during this time
        time.sleep(6)
        
        # 4. Step B: Join Game (Deep Link)
        # Now that the App is (hopefully) open and logged in as our desired user,
        # 4. Step B: Join Game (Deep Link)
        # We use a robust deep link format for joining.
        join_url = f"roblox://placeId={clean_place_id}"
        
        if vip_url:
            code = None
            # Handle old format: ?privateServerLinkCode=...
            if "privateServerLinkCode=" in vip_url:
                try:
                    code = vip_url.split("privateServerLinkCode=")[1].split("&")[0]
                except (IndexError, ValueError):
                    pass
            # Handle new format: /share?code=...
            elif "/share?code=" in vip_url:
                try:
                    code = vip_url.split("code=")[1].split("&")[0]
                except (IndexError, ValueError):
                    pass
            # Fallback for raw code
            elif len(vip_url.strip()) > 5 and "/" not in vip_url:
                code = vip_url.strip()
            
            if code:
                # For private servers, we append &linkCode= (modern) or &privateServerLinkCode=
                # Adding both to be safe, as behavior varies across clients
                join_url += f"&linkCode={code}&privateServerLinkCode={code}"
                logging.info(f"Step 2: Joining VIP Server (Code: {code[:8]}...)")
            else:
                logging.info(f"Step 2: Joining Public Server (Place: {clean_place_id})")
        else:
            logging.info(f"Step 2: Joining Public Server (Place: {clean_place_id})")

        self._run_command(join_url)
        
        return True, "Launched (Hybrid Mode)."

    def _run_command(self, url):
        """Helper to run a URL via batch file to avoid escaping issues."""
        try:
            bat_path = os.path.join(os.getcwd(), "temp_launch.bat")
            with open(bat_path, "w") as f:
                f.write('@echo off\n')
                f.write(f'start "" "{url}"\n')
                f.write('exit\n')
            subprocess.Popen(bat_path, shell=True)
        except Exception as e:
            logging.error(f"Cmd Error: {e}")

    def check_cookie_validity(self, cookie):
        """
        Simple check to see if we can get user info.
        """
        url = "https://users.roblox.com/v1/users/authenticated"
        headers = {"Cookie": f".ROBLOSECURITY={cookie}"}
        try:
            res = self.session.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                return True, data.get("name", "Unknown")
            return False, None
        except:
            return False, None
