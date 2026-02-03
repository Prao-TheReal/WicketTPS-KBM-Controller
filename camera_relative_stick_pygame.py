import argparse
import ctypes
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import pygame
import vgamepad as vg

import tkinter as tk
from tkinter import ttk

CONFIG_PATH = "config.json"

RESET_DEFAULTS = {
  "poll_hz": 120,
  "output_smoothing": 0.05,
  "deadzone_left": 0.15,
  "deadzone_right": 0.10,
  "mouse_sensitivity": 15.0  # Speed of Right Stick Camera
}

# ----------------------------
# Windows API (Mouse Emulation)
# ----------------------------
user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE = 0x0001

def move_mouse(x, y):
    user32.mouse_event(MOUSEEVENTF_MOVE, int(x), int(y), 0, 0)

def is_key_down(vk_code):
    return (user32.GetAsyncKeyState(vk_code) & 0x8000) != 0

def get_window_titles():
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    GetWindowText = user32.GetWindowTextW
    GetWindowTextLength = user32.GetWindowTextLengthW
    IsWindowVisible = user32.IsWindowVisible

    titles = []
    def foreach_window(hwnd, lParam):
        if IsWindowVisible(hwnd):
            length = GetWindowTextLength(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            titles.append(buff.value)
        return True
    EnumWindows(EnumWindowsProc(foreach_window), 0)
    return titles

# --- KEY MAPPINGS ---
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_W, VK_A, VK_S, VK_D = 0x57, 0x41, 0x53, 0x44
VK_SPACE = 0x20
VK_SHIFT = 0x10
VK_CTRL  = 0x11
VK_TAB   = 0x09
VK_ESC   = 0x1B
VK_E, VK_Q, VK_R, VK_F = 0x45, 0x51, 0x52, 0x46
VK_1, VK_2, VK_3, VK_4 = 0x31, 0x32, 0x33, 0x34
VK_O, VK_P = 0x4F, 0x50

# ----------------------------
# Helpers
# ----------------------------
def clamp(v, lo, hi): return max(lo, min(v, hi))

def rotate_vec(x, y, ang_rad):
    ca, sa = math.cos(ang_rad), math.sin(ang_rad)
    return (x * ca - y * sa), (x * sa + y * ca)

def to_short_axis(v):
    return int(round(clamp(v, -1.0, 1.0) * 32767.0))

def apply_deadzone(x, y, dz):
    mag = math.hypot(x, y)
    if mag < dz: return 0.0, 0.0
    return x, y

# ----------------------------
# Config
# ----------------------------
@dataclass
class Config:
    poll_hz: int = int(RESET_DEFAULTS["poll_hz"])
    output_smoothing: float = float(RESET_DEFAULTS["output_smoothing"])
    deadzone_left: float = float(RESET_DEFAULTS["deadzone_left"])
    deadzone_right: float = float(RESET_DEFAULTS["deadzone_right"])
    mouse_sensitivity: float = float(RESET_DEFAULTS["mouse_sensitivity"])

class SharedState:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.current_yaw_debug = 0.0
        self.connected = False
        self.manual_offset_deg = 0.0 
        self.controller_name = "KBM Mode"
        
        self.debug_ls = (0.0, 0.0)
        self.debug_rs = (0.0, 0.0)

    def snapshot(self) -> Config:
        with self.lock:
            snap = Config()
            for k, v in self.cfg.__dict__.items():
                setattr(snap, k, v)
            return snap
    
    def update_and_save(self, **kwargs):
        with self.lock:
            for k, v in kwargs.items():
                if hasattr(self.cfg, k): setattr(self.cfg, k, v)
            try:
                with open(CONFIG_PATH, "w") as f: json.dump(self.cfg.__dict__, f, indent=2)
            except: pass

# ----------------------------
# Main Worker
# ----------------------------
def controller_loop(state: SharedState):
    pygame.init()
    pygame.joystick.init()
    
    js = None
    count = pygame.joystick.get_count()
    
    for i in range(count):
        temp_js = pygame.joystick.Joystick(i)
        temp_js.init()
        name = temp_js.get_name()
        if "360" in name:
            temp_js.quit()
            continue
        state.controller_name = name
        js = temp_js
        break
    
    if js is None and count > 0:
        js = pygame.joystick.Joystick(0)
        js.init()
    
    if js is None: state.controller_name = "Keyboard/Mouse Only"

    gamepad = vg.VX360Gamepad()
    
    out_lx, out_ly = 0.0, 0.0
    game_yaw_rad = 0.0
    
    last_time = time.perf_counter()

    while not state.stop_event.is_set():
        pygame.event.pump()
        
        now = time.perf_counter()
        dt = now - last_time
        last_time = now
        
        cfg = state.snapshot()
        
        # --- TELEMETRY ---
        found_data = False
        titles = get_window_titles()
        for t in titles:
            if "CameraModData" in t:
                try:
                    parts = t.split("Yaw:")
                    if len(parts) > 1:
                        state.current_yaw_debug = float(parts[1].strip())
                        game_yaw_rad = math.radians(-state.current_yaw_debug)
                        state.connected = True
                        found_data = True
                        break
                except: pass
        if not found_data: state.connected = False

        # --- INPUTS ---
        btn_a = False
        btn_b = False
        btn_x = False
        btn_y = False
        btn_lb = False
        btn_rb = False
        btn_back = False
        btn_start = False
        btn_l3 = False
        btn_r3 = False
        
        dpad_up = False
        dpad_down = False
        dpad_left = False
        dpad_right = False
        
        lx, ly = 0.0, 0.0
        rx, ry = 0.0, 0.0 # Virtual Right Stick (Unused)
        
        mouse_dx, mouse_dy = 0.0, 0.0 # Mouse Emulation
        
        lt_val, rt_val = -1.0, -1.0

        # 1. PHYSICAL CONTROLLER
        if js:
            try:
                # Left Stick
                p_lx = js.get_axis(0)
                p_ly = js.get_axis(1)
                lx, ly = apply_deadzone(p_lx, -p_ly, cfg.deadzone_left)
                
                # Right Stick (MOUSE LOOK)
                # You confirmed Axis 2=X, Axis 3=Y
                p_rx = js.get_axis(2)
                p_ry = js.get_axis(3)
                
                # Apply deadzone for camera
                rs_raw_x, rs_raw_y = apply_deadzone(p_rx, p_ry, cfg.deadzone_right)
                
                # Convert Stick -> Mouse Movement
                mouse_dx += rs_raw_x * cfg.mouse_sensitivity
                mouse_dy += rs_raw_y * cfg.mouse_sensitivity

                # Triggers
                if js.get_numaxes() > 5:
                    lt_val = js.get_axis(4) 
                    rt_val = js.get_axis(5)

                # Buttons
                if js.get_button(0): btn_a = True
                if js.get_button(1): btn_b = True
                if js.get_button(2): btn_x = True
                if js.get_button(3): btn_y = True
                if js.get_button(4): btn_lb = True
                if js.get_button(5): btn_rb = True
                if js.get_button(6): btn_back = True
                if js.get_button(7): btn_start = True
                if js.get_button(8): btn_l3 = True
                if js.get_button(9): btn_r3 = True

                # D-Pad
                if js.get_numhats() > 0:
                    hat = js.get_hat(0)
                    dx, dy = hat[0], hat[1]
                    if dy == 1: dpad_up = True
                    if dy == -1: dpad_down = True
                    if dx == 1: dpad_right = True
                    if dx == -1: dpad_left = True
            except: pass

        # 2. KEYBOARD
        if is_key_down(VK_W): ly += 1.0 
        if is_key_down(VK_S): ly -= 1.0
        if is_key_down(VK_A): lx -= 1.0
        if is_key_down(VK_D): lx += 1.0
        
        if is_key_down(VK_SPACE) or is_key_down(VK_SHIFT): btn_a = True
        if is_key_down(VK_LBUTTON): btn_x = True
        if is_key_down(VK_RBUTTON): lt_val = 1.0
        
        if is_key_down(VK_E): btn_y = True
        if is_key_down(VK_F): btn_b = True
        
        if is_key_down(VK_Q): btn_lb = True
        if is_key_down(VK_R): btn_rb = True
        
        if is_key_down(VK_CTRL): btn_l3 = True
        if is_key_down(VK_MBUTTON): btn_r3 = True
        if is_key_down(VK_TAB): btn_start = True

        if is_key_down(VK_1): dpad_up = True
        if is_key_down(VK_2): dpad_right = True
        if is_key_down(VK_3): dpad_left = True
        if is_key_down(VK_4): dpad_down = True
        
        # 3. ROTATION (Left Stick)
        VK_O, VK_P = 0x4F, 0x50
        if is_key_down(VK_P): state.manual_offset_deg += 90.0 * dt
        if is_key_down(VK_O): state.manual_offset_deg -= 90.0 * dt
        
        total_angle_rad = game_yaw_rad + math.radians(state.manual_offset_deg)
        rlx, rly = rotate_vec(lx, ly, total_angle_rad)

        s = clamp(cfg.output_smoothing, 0.0, 0.99)
        out_lx = out_lx * s + rlx * (1.0 - s)
        out_ly = out_ly * s + rly * (1.0 - s)

        state.debug_ls = (out_lx, out_ly)
        state.debug_rs = (mouse_dx, mouse_dy) # Show Mouse Delta

        # 4. OUTPUT
        
        # A. Apply Mouse Movement (Camera)
        if abs(mouse_dx) > 0.1 or abs(mouse_dy) > 0.1:
            move_mouse(mouse_dx, mouse_dy)

        # B. Apply Virtual Controller (Movement/Actions)
        gamepad.left_joystick(x_value=to_short_axis(out_lx), y_value=to_short_axis(out_ly))
        
        # Triggers
        lt_out = int(clamp((lt_val + 1.0) * 127.5, 0, 255))
        rt_out = int(clamp((rt_val + 1.0) * 127.5, 0, 255))
        gamepad.left_trigger(lt_out)
        gamepad.right_trigger(rt_out)
        
        # Buttons
        if btn_a: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
        
        if btn_b: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_B)
        
        if btn_x: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_X)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_X)
        
        if btn_y: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_Y)
        
        if btn_lb: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER)
        
        if btn_rb: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER)
        
        if btn_back: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK)
        
        if btn_start: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_START)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_START)
        
        if btn_l3: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB)
        
        if btn_r3: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB)
        
        if dpad_up: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
        
        if dpad_down: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
        
        if dpad_left: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
        
        if dpad_right: gamepad.press_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
        else: gamepad.release_button(vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)

        gamepad.update()
        time.sleep(1.0/cfg.poll_hz)

def build_gui(state: SharedState):
    root = tk.Tk()
    root.title("Hybrid v10.0 (MOUSE LOOK)")
    root.geometry("450x400")
    
    tk.Label(root, text="HYBRID: MOUSE LOOK MODE", font=("Arial", 12, "bold")).pack(pady=5)
    
    status_var = tk.StringVar(value="Searching...")
    lbl_status = tk.Label(root, textvariable=status_var, fg="red", font=("Arial", 10))
    lbl_status.pack(pady=2)
    
    # Sensitivity Slider
    frame_sens = tk.Frame(root, relief="groove", borderwidth=1)
    frame_sens.pack(fill="x", padx=10, pady=5)
    
    tk.Label(frame_sens, text="Right Stick Speed (Sensitivity):").pack()
    
    cfg = state.snapshot()
    sens_var = tk.DoubleVar(value=cfg.mouse_sensitivity)
    
    def on_sens_change(_):
        state.update_and_save(mouse_sensitivity=sens_var.get())
        
    scale = ttk.Scale(frame_sens, from_=1.0, to=50.0, variable=sens_var, command=on_sens_change)
    scale.pack(fill="x", padx=5)
    
    sens_lbl = tk.Label(frame_sens, text=f"{sens_var.get():.1f}")
    sens_lbl.pack()
    
    def update_sens_lbl(*args): sens_lbl.config(text=f"{sens_var.get():.1f}")
    sens_var.trace_add("write", update_sens_lbl)

    # Debug Data
    frame_debug = tk.Frame(root, relief="sunken", borderwidth=1)
    frame_debug.pack(fill="x", padx=10, pady=10)
    
    ls_var = tk.StringVar(value="LS: (0.00, 0.00)")
    rs_var = tk.StringVar(value="Mouse Δ: (0.00, 0.00)")
    
    tk.Label(frame_debug, text="LIVE DATA", font=("Arial", 9, "bold")).pack()
    tk.Label(frame_debug, textvariable=ls_var, font=("Courier", 10)).pack()
    tk.Label(frame_debug, textvariable=rs_var, font=("Courier", 10)).pack()

    def update_ui():
        ls = state.debug_ls
        rs = state.debug_rs
        ls_var.set(f"LS: ({ls[0]:.2f}, {ls[1]:.2f})")
        rs_var.set(f"Mouse Δ: ({rs[0]:.1f}, {rs[1]:.1f})")
        
        if state.connected:
            status_var.set("CONNECTED")
            lbl_status.configure(fg="green")
        else:
            status_var.set("SEARCHING...")
            lbl_status.configure(fg="red")
        root.after(50, update_ui)
    update_ui()
    
    return root

def main():
    try:
        # Load Config
        try:
            with open(CONFIG_PATH, "r") as f: data = json.load(f)
        except: data = {}
        cfg = Config()
        for k,v in data.items(): 
            if hasattr(cfg, k): setattr(cfg, k, v)
            
        state = SharedState(cfg)
        worker = threading.Thread(target=controller_loop, args=(state,), daemon=True)
        worker.start()
        root = build_gui(state)
        root.mainloop()
    except KeyboardInterrupt: pass
    finally: state.stop_event.set()

if __name__ == "__main__":
    main()