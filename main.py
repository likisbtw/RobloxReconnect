import customtkinter as ctk
import json
import os
import threading
import time
import psutil
import sys
import glob
import logging
from datetime import datetime
from tkinter import messagebox, StringVar
from launcher import RobloxLauncher

# Configuration
CONFIG_FILE = "accounts.json"
APP_NAME = "Roblox Auto-Reconnect"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class ConsoleLogger:
    def __init__(self, textbox):
        self.textbox = textbox

    def write(self, message):
        if message.strip():
            timestamp = datetime.now().strftime("%H:%M:%S")
            formatted_msg = f"[{timestamp}] {message.strip()}\n"
            self.textbox.after(0, self._append_text, formatted_msg)
        self.flush()

    def _append_text(self, text):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def flush(self):
        pass

class AccountManager:
    def __init__(self, filepath):
        self.filepath = filepath
        self.accounts = self.load_accounts()

    def load_accounts(self):
        if not os.path.exists(self.filepath):
            return []
        try:
            with open(self.filepath, 'r') as f:
                return json.load(f)
        except:
            return []

    def save_accounts(self):
        with open(self.filepath, 'w') as f:
            json.dump(self.accounts, f, indent=4)

    def add_account(self, name, cookie, place_id, vip_url=""):
        self.accounts.append({
            "name": name,
            "cookie": cookie,
            "place_id": place_id,
            "vip_url": vip_url,
            "enabled": True
        })
        self.save_accounts()

    def update_account(self, index, data):
        if 0 <= index < len(self.accounts):
            self.accounts[index] = data
            self.save_accounts()

    def delete_account(self, index):
        if 0 <= index < len(self.accounts):
            del self.accounts[index]
            self.save_accounts()

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("800x600")
        
        self.account_manager = AccountManager(CONFIG_FILE)
        self.launcher = RobloxLauncher()
        self.watchdog_active = False
        self.watchdog_thread = None
        self.active_pids = {}  # index -> PID tracking
        self.active_logs = {}  # index -> {"path": str, "last_size": int}
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1) # Give console some weight too

        # Header
        self.header_frame = ctk.CTkFrame(self)
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        
        self.title_label = ctk.CTkLabel(self.header_frame, text=APP_NAME, font=("Roboto", 20, "bold"))
        self.title_label.pack(side="left", padx=10)
        
        # Multi-Instance Toggle
        self.multi_instance_var = ctk.BooleanVar(value=False)
        self.multi_instance_chk = ctk.CTkCheckBox(self.header_frame, text="Multi-Instance", variable=self.multi_instance_var)
        self.multi_instance_chk.pack(side="left", padx=10)
        
        self.watchdog_btn = ctk.CTkButton(self.header_frame, text="Start Watchdog", command=self.toggle_watchdog, fg_color="green")
        self.watchdog_btn.pack(side="right", padx=10)

        # Account List
        self.scroll_frame = ctk.CTkScrollableFrame(self, label_text="Accounts")
        self.scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        
        # Footer
        self.footer_frame = ctk.CTkFrame(self)
        self.footer_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        
        self.add_btn = ctk.CTkButton(self.footer_frame, text="Add Account", command=self.open_add_dialog)
        self.add_btn.pack(side="left", padx=10)
        
        # Console
        self.console_textbox = ctk.CTkTextbox(self, height=150, state="disabled", font=("Consolas", 12))
        self.console_textbox.grid(row=3, column=0, sticky="nsew", padx=10, pady=(0, 10))
        
        # Redirect stdout/stderr
        sys.stdout = ConsoleLogger(self.console_textbox)
        sys.stderr = ConsoleLogger(self.console_textbox)
        
        # Configure logging to use the redirected streams
        logging.basicConfig(level=logging.INFO, format='%(message)s', force=True)
        
        print("Application Initialized.")
        
        self.reload_list()

    def _get_roblox_pids(self):
        """Returns a set of all current RobloxPlayerBeta.exe PIDs."""
        pids = set()
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and 'RobloxPlayerBeta' in proc.info['name']:
                    pids.add(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return pids

    def _find_latest_roblox_log(self):
        """Returns the path to the most recently created Roblox log file."""
        log_dir = os.path.join(os.getenv('LOCALAPPDATA'), 'Roblox', 'logs')
        if not os.path.exists(log_dir):
            return None
        list_of_files = glob.glob(os.path.join(log_dir, '*.log'))
        if not list_of_files:
            return None
        return max(list_of_files, key=os.path.getctime)

    def reload_list(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()

        for idx, acc in enumerate(self.account_manager.accounts):
            start_cmd = lambda i=idx: self.launch_account(i)
            edit_cmd = lambda i=idx: self.open_edit_dialog(i)
            del_cmd = lambda i=idx: self.delete_account(i)
            
            row = ctk.CTkFrame(self.scroll_frame)
            row.pack(fill="x", pady=5)
            
            name_lbl = ctk.CTkLabel(row, text=acc['name'], font=("Arial", 14, "bold"), width=150, anchor="w")
            name_lbl.pack(side="left", padx=10)
            
            id_lbl = ctk.CTkLabel(row, text=f"ID: {acc['place_id']}", text_color="gray")
            id_lbl.pack(side="left", padx=10)
            
            # Buttons
            ctk.CTkButton(row, text="Launch", width=80, command=start_cmd).pack(side="right", padx=5)
            ctk.CTkButton(row, text="Edit", width=60, fg_color="gray", command=edit_cmd).pack(side="right", padx=5)
            ctk.CTkButton(row, text="Del", width=60, fg_color="red", command=del_cmd).pack(side="right", padx=5)

    def launch_account(self, index, from_watchdog=False):
        acc = self.account_manager.accounts[index]
        print(f"Launching {acc['name']}...")
        
        # Capture current state of the checkbox
        is_multi = self.multi_instance_var.get()
        
        def _launch():
            # 1. Get PIDs before launch
            pre_launch_pids = self._get_roblox_pids()
            
            success, msg = self.launcher.launch_game(
                acc['cookie'], 
                acc['place_id'], 
                acc['vip_url'], 
                multi_instance=is_multi
            )
            
            if success:
                print(f"Launched {acc['name']}. Waiting for PID...")
                # 2. Polling for new PID (up to 30 seconds)
                new_pid = None
                for _ in range(30):
                    current_pids = self._get_roblox_pids()
                    diff = current_pids - pre_launch_pids
                    if diff:
                        new_pid = list(diff)[0] # Usually only one
                        break
                    time.sleep(1)
                
                if new_pid:
                    print(f"Detected PID {new_pid} for {acc['name']}")
                    self.active_pids[index] = new_pid
                    
                    # Track log file for error monitoring
                    log_file = self._find_latest_roblox_log()
                    if log_file:
                        print(f"Tracking log: {os.path.basename(log_file)}")
                        self.active_logs[index] = {
                            "path": log_file,
                            "last_pos": os.path.getsize(log_file)
                        }
                else:
                    print(f"Warning: Could not detect PID for {acc['name']}")
            else:
                print(f"Error launching {acc['name']}: {msg}")
                if not from_watchdog:
                    self.after(0, lambda: messagebox.showerror("Launch Error", msg))

        if from_watchdog:
            _launch()
        else:
            threading.Thread(target=_launch, daemon=True).start()

    def delete_account(self, index):
        if messagebox.askyesno("Confirm", "Delete this account?"):
            self.account_manager.delete_account(index)
            self.reload_list()

    def open_add_dialog(self):
        self.open_edit_dialog(-1)

    def open_edit_dialog(self, index):
        is_edit = index >= 0
        acc = self.account_manager.accounts[index] if is_edit else {"name":"", "cookie":"", "place_id":"", "vip_url":""}
        
        dialog = ctk.CTkToplevel(self)
        dialog.title("Edit Account" if is_edit else "Add Account")
        dialog.geometry("400x400")
        dialog.attributes("-topmost", True)
        
        ctk.CTkLabel(dialog, text="Name").pack(pady=5)
        name_entry = ctk.CTkEntry(dialog)
        name_entry.pack(pady=5)
        name_entry.insert(0, acc['name'])
        
        ctk.CTkLabel(dialog, text="Cookie (.ROBLOSECURITY)").pack(pady=5)
        cookie_entry = ctk.CTkEntry(dialog, show="*") # Masked for privacy
        cookie_entry.pack(pady=5)
        cookie_entry.insert(0, acc['cookie'])

        ctk.CTkLabel(dialog, text="Place ID").pack(pady=5)
        place_entry = ctk.CTkEntry(dialog)
        place_entry.pack(pady=5)
        place_entry.insert(0, acc['place_id'])

        ctk.CTkLabel(dialog, text="VIP Link (Optional)").pack(pady=5)
        vip_entry = ctk.CTkEntry(dialog)
        vip_entry.pack(pady=5)
        vip_entry.insert(0, acc['vip_url'])

        def save():
            raw_place = place_entry.get().strip()
            
            # Auto-extract Place ID from URL if user pasted a link
            extracted_id = raw_place
            if "roblox.com/games/" in raw_place:
                try:
                    # format: .../games/123456/Name...
                    parts = raw_place.split("/games/")
                    if len(parts) > 1:
                        sub = parts[1]
                        # take the first part until next slash
                        extracted_id = sub.split("/")[0]
                except:
                    pass
            
            # Validations
            if not extracted_id.isdigit():
                 messagebox.showerror("Error", "Place ID must be a number (e.g., 123456) or a valid Roblox Game URL.")
                 return

            new_data = {
                "name": name_entry.get(),
                "cookie": cookie_entry.get(),
                "place_id": extracted_id,
                "vip_url": vip_entry.get(),
                "enabled": True
            }
            if not new_data["name"] or not new_data["cookie"]:
                messagebox.showerror("Error", "Name and Cookie are required.")
                return
            
            # Verify cookie?
            valid, user = self.launcher.check_cookie_validity(new_data["cookie"])
            if not valid:
                if not messagebox.askyesno("Warning", "Cookie seems invalid or expired. Continue anyway?"):
                    return
            else:
                 # Only append if not already there to avoid duplicates on edit
                 if f"({new_data['name']})" not in user:
                     new_data["name"] = user + f" ({new_data['name']})" 

            if is_edit:
                self.account_manager.update_account(index, new_data)
            else:
                self.account_manager.add_account(new_data['name'], new_data['cookie'], new_data['place_id'], new_data['vip_url'])
            
            self.reload_list()
            dialog.destroy()

        ctk.CTkButton(dialog, text="Save", command=save).pack(pady=20)

    def toggle_watchdog(self):
        if self.watchdog_active:
            self.watchdog_active = False
            self.watchdog_btn.configure(text="Start Watchdog", fg_color="green")
        else:
            self.watchdog_active = True
            self.watchdog_btn.configure(text="Stop Watchdog", fg_color="red")
            self.watchdog_thread = threading.Thread(target=self.watchdog_loop, daemon=True)
            self.watchdog_thread.start()

    def watchdog_loop(self):
        print("Watchdog started.")
        ERR_KEYWORDS = ["Kick", "Disconnected", "Security", "Connection Error", "Authentication failed", "illegal", "exploit"]
        
        while self.watchdog_active:
            enabled_accounts = self.account_manager.accounts
            # Silent checking unless something happens
            # print(f"[Watchdog] Checking {len(enabled_accounts)} accounts...")
            
            for i, acc in enumerate(enabled_accounts):
                # 1. Check if PID is still alive
                pid = self.active_pids.get(i)
                is_running = False
                
                if pid:
                    try:
                        proc = psutil.Process(pid)
                        if proc.is_running() and 'RobloxPlayerBeta' in proc.name():
                            is_running = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        is_running = False
                
                # 2. Check logs for errors if still running
                if is_running and i in self.active_logs:
                    log_entry = self.active_logs[i]
                    log_path = log_entry["path"]
                    if os.path.exists(log_path):
                        try:
                            file_size = os.path.getsize(log_path)
                            if file_size > log_entry["last_pos"]:
                                with open(log_path, 'r', errors='ignore') as f:
                                    f.seek(log_entry["last_pos"])
                                    new_lines = f.readlines()
                                    log_entry["last_pos"] = file_size
                                    
                                    for line in new_lines:
                                        if any(kw in line for kw in ERR_KEYWORDS):
                                            print(f"[Watchdog] ERROR detected in log for {acc['name']}: {line.strip()}")
                                            is_running = False # Trigger relaunch
                                            # Kill the process if it's still hung there
                                            try:
                                                psutil.Process(pid).kill()
                                            except: pass
                                            break
                        except Exception as e:
                            print(f"[Watchdog] Error reading log for {acc['name']}: {e}")

                if not is_running:
                    print(f"[Watchdog] Account {acc['name']} needs relaunch. (PID: {pid})")
                    if i in self.active_pids: del self.active_pids[i]
                    if i in self.active_logs: del self.active_logs[i]
                    self.launch_account(i, from_watchdog=True)
                    time.sleep(15) 
            
            time.sleep(15) # Check cycle every 15 seconds

if __name__ == "__main__":
    app = App()
    app.mainloop()
