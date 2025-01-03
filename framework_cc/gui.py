"""GUI module for Framework Control Center."""

import asyncio
import json
import webbrowser
from pathlib import Path
from typing import Optional
import customtkinter as ctk
from PIL import Image
import threading
import subprocess
import sys
import os
import logging
from tkinter import font
from datetime import datetime
import shutil
import winreg
import ctypes
import tkinter.messagebox as messagebox
import time

from .models import SystemConfig, HardwareMetrics
from .hardware import HardwareMonitor
from .display import DisplayManager
from .detector import ModelDetector
from .logger import logger, check_and_rotate_log
from .translations import get_text, language_names
from .power_plan import PowerManager, PowerProfile

logger = logging.getLogger(__name__)

def get_resource_path(relative_path):
    """Get absolute path to resource for PyInstaller bundled app."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)

def load_custom_font(language_code: str = "en") -> tuple:
    """Load custom font based on language and return font family name."""
    try:
        # Get the absolute path to the fonts using get_resource_path
        font_path = get_resource_path(os.path.join("fonts", "Ubuntu-Regular.ttf"))
        klingon_font_path = get_resource_path(os.path.join("fonts", "klingon font.ttf"))
        
        logger.debug(f"Looking for fonts at: {font_path} and {klingon_font_path}")
        
        # Load the appropriate font based on language
        if language_code == "tlh" and os.path.exists(klingon_font_path):
            # Register Klingon font with larger size
            try:
                ctk.FontManager.load_font(klingon_font_path)
                logger.info(f"Loaded Klingon font from: {klingon_font_path}")
                return ("klingon font", 14)  # Increased from default
            except Exception as e:
                logger.error(f"Failed to load Klingon font: {e}")
                return ("Helvetica", 13)  # Fallback
        elif os.path.exists(font_path):
            # Register Ubuntu font with larger size
            try:
                ctk.FontManager.load_font(font_path)
                logger.info(f"Loaded Ubuntu font from: {font_path}")
                return ("Ubuntu-Regular", 13)  # Increased from 10
            except Exception as e:
                logger.error(f"Failed to load Ubuntu font: {e}")
                return ("Helvetica", 13)  # Fallback
        else:
            logger.warning(f"Font files not found at: {font_path} or {klingon_font_path}")
            return ("Helvetica", 13)  # Fallback
    except Exception as e:
        logger.error(f"Error loading custom font: {e}")
        return ("Helvetica", 13)  # Fallback

def install_system_fonts() -> None:
    """Install application fonts to Windows system if they don't exist."""
    try:
        # Check for admin privileges
        if not ctypes.windll.shell32.IsUserAnAdmin():
            logger.info("Admin privileges required to install fonts")
            return
            
        # Get Windows Fonts directory
        windows_fonts_dir = Path(os.environ["WINDIR"]) / "Fonts"
            
        # Get application fonts directory
        app_fonts_dir = Path("fonts")
        if not app_fonts_dir.exists():
            logger.warning("Fonts directory not found")
            return
            
        # List of fonts to install
        fonts_to_install = {
            "Ubuntu-Regular.ttf": "Ubuntu Regular (TrueType)",
            "klingon font.ttf": "Klingon (TrueType)"
        }
        
        for font_file, reg_name in fonts_to_install.items():
            font_path = app_fonts_dir / font_file
            system_font_path = windows_fonts_dir / font_file
            
            if not font_path.exists():
                logger.warning(f"Font file not found: {font_file}")
                continue
                
            try:
                # Check if font is already installed
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", 0, winreg.KEY_READ) as key:
                    winreg.QueryValueEx(key, reg_name)
                logger.debug(f"Font already installed: {font_file}")
            except FileNotFoundError:
                try:
                    # Font not installed, copy it to Windows Fonts directory
                    logger.info(f"Installing font: {font_file}")
                    shutil.copy2(font_path, system_font_path)
                    
                    # Add font to registry
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts", 0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, font_file)
                        
                    logger.info(f"Font installed successfully: {font_file}")
                except PermissionError:
                    logger.warning(f"Permission denied while installing font: {font_file}")
                except Exception as e:
                    logger.error(f"Error installing font {font_file}: {e}")
                
    except Exception as e:
        logger.error(f"Error installing fonts: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")

class FrameworkControlCenter(ctk.CTk):
    # Class-level variables for tray icon management
    _tray_lock = threading.Lock()
    _tray_instance = None
    _open_windows = []  # Track all open windows
    _last_notification_time = 0  # Track last notification time
    _notification_cooldown = 5  # Cooldown in seconds
    
    def __init__(self):
        # Hide console window on Windows
        if sys.platform.startswith('win'):
            import ctypes
            ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        
        super().__init__()
        
        # Initialize event loop first
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Ensure we're in the correct directory
        if getattr(sys, 'frozen', False):
            os.chdir(os.path.dirname(sys.executable))
        
        # Install system fonts if needed
        install_system_fonts()
        
        FrameworkControlCenter._open_windows.append(self)  # Add main window to list
        
        # Load configuration from settings.json
        self.config_path = Path("configs") / "settings.json"
        self.config = self._load_config()
        
        # Setup theme and colors
        self._setup_theme()
        
        # Configuration de la fenêtre
        self.title("Framework Control Center")
        self.geometry("300x700")  # Increased height from 650 to 700
        self.resizable(False, False)
        self.attributes('-topmost', True)  # Keep window on top when active
        
        # Configurer l'icône - Use absolute path and add delay
        try:
            icon_path = os.path.abspath("assets/logo.ico")
            if os.path.exists(icon_path):
                if sys.platform.startswith('win'):
                    self.after(500, lambda: self.iconbitmap(icon_path))
                else:
                    self.iconbitmap(icon_path)
                logger.info(f"Icon set successfully from: {icon_path}")
            else:
                logger.error(f"Icon file not found at: {icon_path}")
        except Exception as e:
            logger.error(f"Failed to set window icon: {e}")
        
        # Configurer le style de la fenêtre
        self.configure(fg_color=self.colors.background.main)
        
        # Bind minimize to tray for the window minimize button
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", lambda e: self._minimize_to_tray() if self.config.minimize_to_tray else None)
        
        # Positionner la fenêtre dans le coin inférieur droit
        self.after(1000, self._position_window)  # Delay window positioning
        
        # Initialize model detection first
        detector = ModelDetector()
        self.model = detector.detect_model()
        if not self.model:
            logger.error("No compatible Framework laptop detected")
            raise RuntimeError("No compatible Framework laptop detected")
            
        # Load custom font
        self.current_font = load_custom_font(self.config.language)
        
        # Initialize managers with detected model
        self.hardware = HardwareMonitor(self.model.name)
        self.hardware.set_update_interval(self.config.monitoring_interval)
        self.power = PowerManager(self.model)
        self.display = DisplayManager(model=self.model)

        # Setup UI
        self._create_widgets()
        self._setup_hotkeys()
        
        # Setup tray icon with delay
        self.after(1500, self._setup_tray)

        # Start monitoring with delay
        self.after(2000, lambda: self.after(self.config.monitoring_interval, self._update_metrics))
        
        # Initialize default profiles after a delay
        self.after(2500, self._initialize_default_profiles)

        # Start log file check timer with delay
        self.after(3000, self._check_log_file_size)

    def _get_dpi_scale(self) -> float:
        """Get the current DPI scale factor."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            awareness = ctypes.c_int()
            ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
            dpi = user32.GetDpiForSystem()
            return dpi / 96.0  # 96 is the base DPI
        except Exception as e:
            logger.error(f"Error getting DPI scale: {e}")
            return 1.0

    def _position_window(self) -> None:
        """Position the window in the bottom right corner."""
        try:
            # Get DPI scale
            dpi_scale = self._get_dpi_scale()
            
            # Get screen dimensions (in actual pixels)
            screen_width = int(self.winfo_screenwidth() * dpi_scale)
            screen_height = int(self.winfo_screenheight() * dpi_scale)
            
            # Get window dimensions
            window_width = 300
            window_height = 700
            
            # Use saved position if available, otherwise calculate default position
            if self.config.window_position["x"] == 0 and self.config.window_position["y"] == 0:
                # Calculate default position (bottom right)
                # Increased right margin to align with system tray
                x = int((screen_width - window_width - 170) / dpi_scale)  # Changed from 20 to 170
                y = int((screen_height - window_height - 60) / dpi_scale)
                self.config.window_position = {"x": x, "y": y}
            else:
                # Convert saved position from logical to physical coordinates
                x = int(self.config.window_position["x"] / dpi_scale)
                y = int(self.config.window_position["y"] / dpi_scale)
            
            # Ensure window is visible on screen
            x = max(0, min(x, int((screen_width - window_width) / dpi_scale)))
            y = max(0, min(y, int((screen_height - window_height) / dpi_scale)))
            
            # Set window position
            self.geometry(f"{window_width}x{window_height}+{x}+{y}")
            
        except Exception as e:
            logger.error(f"Error positioning window: {e}")
            # Use default center position as fallback
            self.center_window()

    def _save_window_position(self) -> None:
        """Save current window position to config."""
        try:
            # Get DPI scale
            dpi_scale = self._get_dpi_scale()
            
            # Get window geometry
            geometry = self.geometry()
            # Parse geometry string (format: 'widthxheight+x+y')
            x = int(geometry.split('+')[1])
            y = int(geometry.split('+')[2])
            
            # Convert physical coordinates to logical coordinates
            x = int(x * dpi_scale)
            y = int(y * dpi_scale)
            
            # Update config
            self.config.window_position = {"x": x, "y": y}
            self._save_config()
            
            # Show confirmation message
            messagebox.showinfo(
                get_text(self.config.language, "success"),
                "Window position saved successfully"
            )
            
            logger.debug(f"Window position saved: x={x}, y={y} (DPI scale: {dpi_scale})")
        except Exception as e:
            logger.error(f"Error saving window position: {e}")
            messagebox.showerror(
                get_text(self.config.language, "error"),
                "Failed to save window position"
            )

    def _start_drag(self, event) -> None:
        """Start window drag."""
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def _on_drag(self, event) -> None:
        """Handle window drag."""
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.geometry(f"+{x}+{y}")
        # Save position after drag
        self._save_window_position()

    def _setup_theme(self) -> None:
        """Setup the application theme."""
        ctk.set_appearance_mode("dark")  # Base appearance mode
        ctk.set_default_color_theme("blue")  # Base color theme
        
        # Load theme colors from config
        theme = self.config.load_theme()
        self.colors = theme.colors
        self.theme_fonts = theme.fonts
        self.spacing = theme.spacing
        self.radius = theme.radius
        
        # Garder une référence aux boutons actifs
        self.active_buttons = {
            "profile": None,
            "refresh": None
        }

    def _create_widgets(self) -> None:
        """Create all GUI widgets."""
        # Main container with dark background and rounded corners
        self.container = ctk.CTkFrame(
            self,
            fg_color=self.colors.background.main,
            corner_radius=10
        )
        self.container.pack(fill="both", expand=True, padx=0, pady=0)

        # Create event loop for async operations
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        # Power profiles
        self._create_power_profiles()

        # Refresh rate controls
        self._create_refresh_controls()

        # System metrics
        self._create_metrics_display()

        # Grand spacer pour pousser les éléments vers le bas
        spacer = ctk.CTkFrame(self.container, fg_color="transparent", height=20)
        spacer.pack(fill="x")

        # Additional buttons
        self._create_utility_buttons()

        # Petit spacer avant brightness
        spacer2 = ctk.CTkFrame(self.container, fg_color="transparent", height=20)
        spacer2.pack(fill="x")

        # Brightness control
        self._create_brightness_control()

        # Battery status
        self._create_battery_status()

        # Additional buttons
        buttons_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        buttons_frame.pack(fill="x", padx=10, pady=5)

    def _create_power_profiles(self) -> None:
        """Create power profile buttons."""
        profiles_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        profiles_frame.pack(fill="x", padx=10, pady=5)

        # Créer un sous-frame pour les boutons avec distribution égale
        buttons_frame = ctk.CTkFrame(profiles_frame, fg_color=self.colors.background.main)
        buttons_frame.pack(fill="x", padx=5)
        buttons_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Charger les icônes
        icons = {
            "Silent": ctk.CTkImage(Image.open(get_resource_path("assets/eco.png")), size=(24, 24)),
            "Balanced": ctk.CTkImage(Image.open(get_resource_path("assets/balanced.png")), size=(24, 24)),
            "Boost": ctk.CTkImage(Image.open(get_resource_path("assets/performance.png")), size=(24, 24))
        }

        self.profile_buttons = {}
        for i, profile in enumerate(["Silent", "Balanced", "Boost"]):
            translated_text = get_text(self.config.language, f"power_profiles.{profile.lower()}")
            btn = ctk.CTkButton(
                buttons_frame,
                text=translated_text,
                image=icons[profile],
                compound="top",  # Place l'icône au-dessus du texte
                command=lambda p=profile: self._set_power_profile_sync(p),
                fg_color=self.colors.button.primary,
                hover_color=self.colors.hover,
                text_color=self.colors.text.primary,
                height=60,  # Plus haut pour accommoder l'icône au-dessus du texte
                width=90,
                border_width=2,
                border_color=self.colors.border.inactive,
                corner_radius=10  # Ajout des coins arrondis
            )
            btn.grid(row=0, column=i, padx=3)
            self.profile_buttons[profile] = btn

            # Définir le profil actif par défaut
            if profile == self.config.current_profile:
                self._update_button_state("profile", btn)

    def _create_refresh_controls(self) -> None:
        """Create refresh rate control buttons."""
        refresh_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        refresh_frame.pack(fill="x", padx=10, pady=5)

        # Create a sub-frame for buttons with equal distribution
        buttons_frame = ctk.CTkFrame(refresh_frame, fg_color=self.colors.background.main)
        buttons_frame.pack(fill="x", padx=5)

        # Get valid refresh rates from display manager
        refresh_rates = ["Auto"] + self.display._valid_rates
        
        # Configure grid columns based on number of buttons
        num_buttons = len(refresh_rates)
        for i in range(num_buttons):
            buttons_frame.grid_columnconfigure(i, weight=1)

        self.refresh_buttons = {}
        for i, mode in enumerate(refresh_rates):
            # Convert mode to correct translation key format
            mode_key = mode.lower()
            if mode_key != "auto":
                mode_key = f"{mode_key}hz"  # Add 'hz' suffix for numeric rates
            translated_text = get_text(self.config.language, f"refresh_rates.{mode_key}")
            
            btn = ctk.CTkButton(
                buttons_frame,
                text=translated_text,
                command=lambda m=mode: self._set_refresh_rate_sync(m),
                fg_color=self.colors.button.primary,
                hover_color=self.colors.hover,
                text_color=self.colors.text.primary,
                height=35,
                width=90,
                border_width=2,
                border_color=self.colors.border.inactive,
                corner_radius=self.radius.normal
            )
            btn.grid(row=0, column=i, padx=3)
            self.refresh_buttons[mode] = btn

            # Set active mode by default
            if mode == self.config.refresh_rate_mode:
                self._update_button_state("refresh", btn)

    def _create_metrics_display(self) -> None:
        """Create system metrics display."""
        metrics_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        metrics_frame.pack(fill="x", padx=10, pady=5)

        # Create labels and progress bars for metrics
        self.metric_bars = {}
        self.metric_labels = {}
        
        # Métriques de base toujours affichées
        metrics = [
            ("CPU", "cpu_load", "%"),
            ("CPU TEMP", "cpu_temp", "°C"),
            ("RAM", "ram_usage", "%"),
            ("iGPU", "igpu_load", "%"),
            ("iGPU TEMP", "igpu_temp", "°C")
        ]

        # Ajouter les métriques dGPU pour le modèle 16_AMD
        if self.model.has_dgpu:
            logger.info(f"Detected {self.model.name} with dGPU, adding dGPU metrics to display")
            metrics.extend([
                ("dGPU", "dgpu_load", "%"),
                ("dGPU TEMP", "dgpu_temp", "°C")
            ])
            logger.debug("Final metrics list: %s", metrics)
        else:
            logger.info(f"Model {self.model.name} has no dGPU, skipping dGPU metrics")

        # Créer les widgets pour chaque métrique
        for label, key, unit in metrics:
            frame = ctk.CTkFrame(metrics_frame, fg_color=self.colors.background.main)
            frame.pack(fill="x", pady=2)

            # Label with value
            label_text = ctk.CTkLabel(
                frame, 
                text=f"{label}: 0{unit}", 
                text_color=self.colors.text.primary,
                anchor="w",  # Align text to the left
                width=150  # Fixed width for consistent alignment
            )
            label_text.pack(side="left", padx=5)
            self.metric_labels[key] = label_text
            logger.debug(f"Created metric label: {label} -> {key}")

            # Progress bar
            progress = ctk.CTkProgressBar(
                frame,
                progress_color=self.colors.progress.bar,
                fg_color=self.colors.progress.background,
                height=15,
                width=120
            )
            progress.pack(side="right", padx=5)
            progress.set(0)
            self.metric_bars[key] = progress
            logger.debug(f"Created progress bar for: {key}")
            
            # Add a small vertical spacer between metrics
            spacer = ctk.CTkFrame(metrics_frame, fg_color=self.colors.background.main, height=2)
            spacer.pack(fill="x", pady=1)

    def _create_utility_buttons(self) -> None:
        """Create utility buttons."""
        buttons = [
            ("Keyboard", self._open_keyboard_config),
            (get_text(self.config.language, "utility_buttons.updates_manager", "Updates manager"), self._open_updates_manager),
            ("Settings", self._open_settings)
        ]

        for text, command in buttons:
            btn = ctk.CTkButton(
                self.container,
                text=text,
                command=command,
                fg_color=self.colors.button.primary,
                hover_color=self.colors.hover,
                height=30,
                text_color=self.colors.text.primary,
                corner_radius=10
            )
            btn.pack(fill="x", padx=10, pady=2)
            if text in ["Keyboard", get_text(self.config.language, "utility_buttons.updates_manager", "Updates manager"), "Settings"]:
                btn.configure(width=120)

    def _create_brightness_control(self) -> None:
        """Create brightness control slider."""
        brightness_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        brightness_frame.pack(fill="x", padx=10, pady=10)

        label = ctk.CTkLabel(brightness_frame, text="BRIGHTNESS:", text_color=self.colors.text.primary)
        label.pack(side="left")

        self.brightness_value = ctk.CTkLabel(
            brightness_frame,
            text="VALUE: 100%",
            text_color=self.colors.text.primary
        )
        self.brightness_value.pack(side="right")

        self.brightness_slider = ctk.CTkSlider(
            brightness_frame,
            from_=0,
            to=100,
            command=self._on_brightness_change,
            progress_color=self.colors.progress.bar,
            button_color=self.colors.button.primary,
            button_hover_color=self.colors.hover,
            fg_color=self.colors.background.secondary,  # Couleur de fond de la barre
            border_color=self.colors.border.inactive,   # Couleur de la bordure
            corner_radius=10
        )
        self.brightness_slider.pack(fill="x", padx=5)
        self.brightness_slider.set(100)

    def _update_metrics(self) -> None:
        """Update system metrics display."""
        if not hasattr(self, 'loop') or not self.winfo_exists():
            logger.warning("Skipping metrics update - window destroyed or loop not initialized")
            return

        async def update():
            try:
                metrics = await self.hardware.get_metrics()
                logger.debug("Got metrics update")

                # Update progress bars and labels
                for key, bar in self.metric_bars.items():
                    value = getattr(metrics, key, None)
                    if value is not None:
                        if "temp" in key:
                            # Normalize temperature to 0-100 range for progress bar
                            normalized = min(100, max(0, value - 40) * 1.67)
                            bar.set(normalized / 100)
                            
                            # Format label based on temperature type
                            if "cpu" in key:
                                label_text = f"CPU TEMP: {value:.1f}°C"
                            elif "igpu" in key:
                                label_text = f"iGPU TEMP: {value:.1f}°C"
                            elif "dgpu" in key:
                                label_text = f"dGPU TEMP: {value:.1f}°C"
                            else:
                                label_text = f"{key.split('_')[0].upper()} TEMP: {value:.1f}°C"
                            
                            self.metric_labels[key].configure(text=label_text)
                        else:
                            # For load metrics
                            bar.set(value / 100)
                            
                            # Format label based on load type
                            if "cpu" in key:
                                label_text = f"CPU: {value:.1f}%"
                            elif "igpu" in key:
                                label_text = f"iGPU: {value:.1f}%"
                            elif "dgpu" in key:
                                label_text = f"dGPU: {value:.1f}%"
                            elif "ram" in key:
                                label_text = f"RAM: {value:.1f}%"
                            else:
                                label_text = f"{key.split('_')[0].upper()}: {value:.1f}%"
                            
                            self.metric_labels[key].configure(text=label_text)

                # Update battery indicator
                self._update_battery_status(metrics)

            except Exception as e:
                logger.error(f"Error updating metrics: {e}")
                import traceback
                logger.error("Traceback: %s", traceback.format_exc())

        def schedule_next():
            """Schedule the next update if window still exists"""
            if self.winfo_exists():
                self._metrics_after_id = self.after(
                    self.config.monitoring_interval,
                    self._update_metrics
                )

        try:
            # Run the update coroutine
            self.loop.run_until_complete(update())
            # Schedule next update
            schedule_next()
        except Exception as e:
            logger.error(f"Critical error in metrics update: {e}")
            # Try to recover by scheduling next update
            schedule_next()

    def _restart_metrics_update(self) -> None:
        """Restart the metrics update cycle with current interval."""
        try:
            # Cancel existing update if any
            if hasattr(self, '_metrics_after_id'):
                self.after_cancel(self._metrics_after_id)
                delattr(self, '_metrics_after_id')
                
            # Update hardware monitor interval
            if hasattr(self, 'hardware'):
                self.hardware.set_update_interval(self.config.monitoring_interval)
            
            # Start a new update cycle immediately
            self._update_metrics()
            logger.info(f"Metrics update restarted with interval: {self.config.monitoring_interval}ms")
            
        except Exception as e:
            logger.error(f"Error restarting metrics update: {e}")
            # Try to recover by starting a new update cycle
            self._update_metrics()

    def _update_battery_status(self, metrics: HardwareMetrics) -> None:
        """Update battery status display."""
        try:
            # Update battery percentage and charging status
            status = f"BATTERY: {metrics.battery_percentage:.0f}% | {'AC' if metrics.is_charging else 'BATTERY'}"
            self.battery_status.configure(text=status)

            # Update time remaining
            if metrics.is_charging:
                time_text = "Plugged In"
            elif metrics.battery_time_remaining > 0:
                hours = int(metrics.battery_time_remaining / 60)
                minutes = int(metrics.battery_time_remaining % 60)
                time_text = f"Time remaining: {hours:02d}:{minutes:02d}"
            else:
                time_text = "Time remaining: --:--"
            
            self.battery_time.configure(text=time_text)
        except Exception as e:
            logger.error(f"Error updating battery status: {e}")
            self.battery_status.configure(text="BATTERY: --% | --")
            self.battery_time.configure(text="Time remaining: --:--")

    def _on_brightness_change(self, value: float) -> None:
        """Handle brightness slider change."""
        async def update_brightness():
            await self.display.set_brightness(int(value))
        
        if hasattr(self, 'loop'):
            self.loop.run_until_complete(update_brightness())
        self.brightness_value.configure(text=f"ACTUEL: {int(value)}%")

    def _open_keyboard_config(self) -> None:
        """Open keyboard configuration website."""
        webbrowser.open("https://keyboard.frame.work/")

    def _open_updates_manager(self) -> None:
        """Open updates manager window."""
        UpdatesManager(self)

    def _open_settings(self) -> None:
        """Open settings window."""
        self._create_settings_window()

    def _toggle_window(self) -> None:
        """Toggle window visibility."""
        if self.winfo_viewable():
            self.withdraw()
            with self._tray_lock:
                if self.tray_icon is not None:
                    try:
                        self.tray_icon.notify(
                            title="Framework Control Center",
                            message="Application minimized to tray"
                        )
                    except Exception as e:
                        logger.error(f"Error showing tray notification: {e}")
        else:
            self.deiconify()
            self.lift()

    def _on_close(self) -> None:
        """Gérer la fermeture de l'application."""
        # Cleanup
        if hasattr(self.power, 'cleanup'):
            self.power.cleanup()
        
        # Destroy window
        self.quit()

    def _quit_app(self) -> None:
        """Quit the application."""
        try:
            # Stop the tray icon at class level
            with self._tray_lock:
                if FrameworkControlCenter._tray_instance is not None:
                    try:
                        FrameworkControlCenter._tray_instance.stop()
                    except Exception as e:
                        logger.error(f"Error stopping tray icon: {e}")
                    FrameworkControlCenter._tray_instance = None
                    self.tray_icon = None
            
            # Clean up async event loop
            if hasattr(self, 'loop'):
                try:
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    self.loop.close()
                except Exception as e:
                    logger.error(f"Error cleaning up async tasks: {e}")
            
            self.quit()
        except Exception as e:
            logger.error(f"Error during application quit: {e}")
            # Forcer la fermeture en cas d'erreur
            self.quit()

    def _load_config(self) -> SystemConfig:
        """Load configuration from file."""
        try:
            if self.config_path.exists():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                return SystemConfig(**config_data)
            else:
                logger.info("No configuration file found, using defaults")
                return SystemConfig()
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return SystemConfig()

    def _create_default_icon(self) -> None:
        """Create a default tray icon."""
        try:
            icon_path = Path("assets/logo.ico")
            if icon_path.exists():
                return Image.open(icon_path)
        except Exception as e:
            logger.error(f"Failed to load icon: {e}")
            
        # Fallback to creating a new icon if logo.ico is not found
        img = Image.new("RGB", (64, 64), self.colors.primary)
        img.save("assets/icon.png")
        return img

    def _set_power_profile_sync(self, profile_name: str) -> None:
        """Synchronous wrapper for _set_power_profile."""
        try:
            logger.info(f"Applying power profile: {profile_name}")
            logger.debug(f"Current model: {self.model.name}")
            
            if not hasattr(self, 'loop'):
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
            
            # Load profile configuration
            profiles_path = Path("configs/profiles.json")
            if not profiles_path.exists():
                logger.error("Profiles configuration file not found")
                return

            with open(profiles_path) as f:
                config = json.load(f)
                logger.debug(f"Available AMD profiles: {list(config['amd_profiles'].keys())}")
                
                # Get the correct profile based on laptop model
                model_name = str(self.model.name).strip()  # Ensure clean string
                logger.debug(f"Cleaned model name: '{model_name}'")
                
                # Map full model names to profile keys
                model_map = {
                    "Framework 16 AMD": "16_AMD",
                    "Framework 13 AMD": "13_AMD",
                    "Framework 13 Intel": "13_INTEL"
                }
                
                profile_key = model_map.get(model_name)
                if not profile_key:
                    logger.error(f"Unsupported model: '{model_name}'")
                    return
                
                logger.debug(f"Using profile key: {profile_key}")
                
                if profile_key == "16_AMD":
                    if profile_name.lower() not in config["amd_profiles"]["16_AMD"]:
                        logger.error(f"Profile {profile_name} not found for 16_AMD")
                        return
                    profile_data = config["amd_profiles"]["16_AMD"][profile_name.lower()]
                    logger.debug("Selected 16_AMD profile configuration")
                elif profile_key == "13_AMD":
                    if profile_name.lower() not in config["amd_profiles"]["13_AMD"]:
                        logger.error(f"Profile {profile_name} not found for 13_AMD")
                        return
                    profile_data = config["amd_profiles"]["13_AMD"][profile_name.lower()]
                    logger.debug("Selected 13_AMD profile configuration")
                elif profile_key == "13_INTEL":
                    if profile_name.lower() not in config["intel_profiles"]["13_INTEL"]:
                        logger.error(f"Profile {profile_name} not found for 13_INTEL")
                        return
                    profile_data = config["intel_profiles"]["13_INTEL"][profile_name.lower()]
                    logger.debug("Selected 13_INTEL profile configuration")
                
                logger.info(f"Profile configuration loaded: {profile_data}")
                # Remove 'name' from profile_data if it exists to avoid duplicate
                if 'name' in profile_data:
                    del profile_data['name']
                
                # Add required parameters with default values based on profile
                default_params = {
                    'tdp': profile_data.get('stapm_limit', 45000) // 1000,  # Convert from mW to W
                    'cpu_power': profile_data.get('fast_limit', 45000) // 1000,  # Convert from mW to W
                    'gpu_power': 25,  # Default GPU power
                    'boost_enabled': profile_name.lower() != 'silent',  # Disable boost only for silent profile
                    'fan_mode': 'auto',  # Default fan mode
                    'fan_curve': {  # Default fan curve
                        '30': 0,
                        '40': 10,
                        '50': 20,
                        '60': 40,
                        '70': 60,
                        '80': 80,
                        '90': 100
                    }
                }
                
                # Merge default params with profile data
                profile_data.update(default_params)
                
                profile = PowerProfile(
                    name=profile_name,
                    **profile_data
                )
            
            # Log AMD-specific parameters
            if hasattr(profile, 'stapm_limit'):
                logger.info(f"STAPM Limit: {profile.stapm_limit} mW")
            if hasattr(profile, 'fast_limit'):
                logger.info(f"Fast Limit: {profile.fast_limit} mW")
            if hasattr(profile, 'slow_limit'):
                logger.info(f"Slow Limit: {profile.slow_limit} mW")
            if hasattr(profile, 'tctl_temp'):
                logger.info(f"TCTL Temp: {profile.tctl_temp}°C")
            if hasattr(profile, 'vrm_current'):
                logger.info(f"VRM Current: {profile.vrm_current} mA")
            if hasattr(profile, 'vrmmax_current'):
                logger.info(f"VRM Max Current: {profile.vrmmax_current} mA")
            if hasattr(profile, 'vrmsoc_current'):
                logger.info(f"VRM SoC Current: {profile.vrmsoc_current} mA")
            if hasattr(profile, 'vrmsocmax_current'):
                logger.info(f"VRM SoC Max Current: {profile.vrmsocmax_current} mA")
            
            # Log Intel-specific parameters
            if hasattr(profile, 'pl1'):
                logger.info(f"PL1: {profile.pl1} W")
            if hasattr(profile, 'pl2'):
                logger.info(f"PL2: {profile.pl2} W")
            if hasattr(profile, 'tau'):
                logger.info(f"Tau: {profile.tau} s")
            if hasattr(profile, 'cpu_core_offset'):
                logger.info(f"CPU Core Offset: {profile.cpu_core_offset} mV")
            if hasattr(profile, 'gpu_core_offset'):
                logger.info(f"GPU Core Offset: {profile.gpu_core_offset} mV")
            if hasattr(profile, 'max_frequency'):
                logger.info(f"Max Frequency: {profile.max_frequency}")
            
            # Apply profile
            success = self.loop.run_until_complete(self.power.apply_profile(profile))
            
            if success:
                logger.info(f"Successfully applied power profile: {profile_name}")
                self.config.current_profile = profile_name
                
                # Update button states
                if profile_name in self.profile_buttons:
                    self._update_button_state("profile", self.profile_buttons[profile_name])
                    logger.debug(f"Updated button state for profile: {profile_name}")
                
                # Show notification if minimized
                if not self.winfo_viewable():
                    with self._tray_lock:
                        if self.tray_icon is not None:
                            try:
                                self.tray_icon.notify(
                                    title="Profile Changed",
                                    message=f"Power profile changed to {profile_name}"
                                )
                            except Exception as e:
                                logger.error(f"Error showing tray notification: {e}")
            else:
                logger.error(f"Failed to apply power profile: {profile_name}")
                
        except Exception as e:
            logger.error(f"Error setting power profile: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _set_refresh_rate_sync(self, mode: str) -> None:
        """Synchronous wrapper for _set_refresh_rate."""
        if hasattr(self, 'loop'):
            self.loop.run_until_complete(self._set_refresh_rate(mode))

    async def _set_refresh_rate(self, mode: str) -> None:
        """Set display refresh rate."""
        try:
            # Get max rate based on model
            max_rate = "165" if "16" in self.model.name else "60"
            
            # Process mode
            if mode == "Auto":
                actual_mode = "auto"
            else:
                # Clean up mode if it already has Hz
                actual_mode = mode.replace("Hz", "")
            
            logger.debug(f"Setting refresh rate: mode={actual_mode}, max_rate={max_rate}")
            success = await self.display.set_refresh_rate(actual_mode, max_rate)
            
            if success:
                self.config.refresh_rate_mode = mode
                
                # Update button states
                if mode in self.refresh_buttons:
                    self._update_button_state("refresh", self.refresh_buttons[mode])
                
                # Show notification if minimized
                if not self.winfo_viewable():
                    with self._tray_lock:
                        if self.tray_icon is not None:
                            try:
                                self.tray_icon.notify(
                                    title="Refresh Rate Changed",
                                    message=f"Display refresh rate mode changed to {mode}"
                                )
                            except Exception as e:
                                logger.error(f"Error showing tray notification: {e}")
            else:
                logger.error(f"Failed to set refresh rate to {mode}")
                
        except Exception as e:
            logger.error(f"Error setting refresh rate: {e}")
            import traceback
            logger.error("Traceback: %s", traceback.format_exc())

    def _setup_tray(self) -> None:
        """Setup system tray icon and menu."""
        with self._tray_lock:
            # If class already has a tray icon instance, use it
            if FrameworkControlCenter._tray_instance is not None:
                self.tray_icon = FrameworkControlCenter._tray_instance
                return

            try:
                import pystray
                from PIL import Image
                import sys
                import os

                # Get the correct path for the icon file
                if getattr(sys, 'frozen', False):
                    # If the application is run from the exe
                    base_path = sys._MEIPASS
                else:
                    # If the application is run from a Python interpreter
                    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

                # Create tray icon
                icon_path = os.path.join(base_path, "assets", "logo.ico")
                logger.debug(f"Looking for icon at: {icon_path}")
                
                if not os.path.exists(icon_path):
                    logger.error("Icon file not found: %s", icon_path)
                    # Create a default icon as fallback
                    img = Image.new("RGB", (64, 64), self.colors.primary)
                    fallback_path = os.path.join(base_path, "assets", "icon.png")
                    os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
                    img.save(fallback_path)
                    icon_path = fallback_path

                # Create the icon instance
                icon = pystray.Icon(
                    name="Framework CC",
                    icon=Image.open(icon_path),
                    title="Framework Control Center",
                    menu=pystray.Menu(
                        pystray.MenuItem("Show/Hide", self._toggle_window),
                        pystray.MenuItem("Exit", self._quit_app)
                    )
                )

                # Store the icon instance at class level
                FrameworkControlCenter._tray_instance = icon
                self.tray_icon = icon

                # Start the icon in a separate thread
                threading.Thread(target=self.tray_icon.run, daemon=True).start()

            except Exception as e:
                logger.error(f"Error setting up tray icon: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                FrameworkControlCenter._tray_instance = None
                self.tray_icon = None

    def _create_battery_status(self) -> None:
        """Create battery status display."""
        battery_frame = ctk.CTkFrame(self.container, fg_color=self.colors.background.main)
        battery_frame.pack(fill="x", padx=10, pady=5)

        # Battery percentage and charging status
        self.battery_status = ctk.CTkLabel(
            battery_frame,
            text="BATTERY: --% | --",
            text_color=self.colors.text.primary,
            font=("Roboto", 11)
        )
        self.battery_status.pack(side="top", pady=(0, 2))

        # Battery time remaining
        self.battery_time = ctk.CTkLabel(
            battery_frame,
            text="Time remaining: --:--",
            text_color=self.colors.text.primary,
            font=("Roboto", 11)
        )
        self.battery_time.pack(side="top")

    def _setup_hotkeys(self) -> None:
        """Setup global hotkeys."""
        import keyboard
        keyboard.add_hotkey("F12", self._toggle_window)

    def _update_button_state(self, button_type: str, active_button: ctk.CTkButton) -> None:
        """Update button states when a new button becomes active."""
        # Réinitialiser l'ancien bouton actif
        if self.active_buttons[button_type]:
            self.active_buttons[button_type].configure(
                border_color=self.colors.border.inactive,
                text_color=self.colors.text.primary,
                fg_color=self.colors.button.primary
            )

        # Mettre à jour le nouveau bouton actif
        active_button.configure(
            border_color=self.colors.border.active,
            text_color=self.colors.text.primary,
            fg_color=self.colors.hover
        )

        # Sauvegarder le nouveau bouton actif
        self.active_buttons[button_type] = active_button

    def _update_window_text(self) -> None:
        """Update all window text with current language."""
        try:
            # Update window title
            self.title(get_text(self.config.language, "window_title"))
            
            # Update profile buttons if they exist
            if hasattr(self, 'profile_buttons') and self.profile_buttons:
                for profile in self.profile_buttons:
                    button = self.profile_buttons[profile]
                    translated_text = get_text(self.config.language, f"power_profiles.{profile.lower()}")
                    button.configure(text=translated_text)
            
            # Update refresh rate buttons if they exist
            if hasattr(self, 'refresh_buttons') and self.refresh_buttons:
                for mode in self.refresh_buttons:
                    button = self.refresh_buttons[mode]
                    # Convert mode to correct translation key format
                    mode_key = mode.lower()
                    if mode_key != "auto":
                        mode_key = f"{mode_key}hz"  # Add 'hz' suffix for numeric rates
                    translated_text = get_text(self.config.language, f"refresh_rates.{mode_key}")
                    button.configure(text=translated_text)
            
            # Update utility buttons if they exist
            if hasattr(self, 'keyboard_button'):
                self.keyboard_button.configure(text=get_text(self.config.language, "utility_buttons.keyboard"))
            if hasattr(self, 'updates_button'):
                self.updates_button.configure(text=get_text(self.config.language, "utility_buttons.updates_manager"))
            if hasattr(self, 'settings_button'):
                self.settings_button.configure(text=get_text(self.config.language, "utility_buttons.settings"))
            
            logger.debug("Window text updated to language: " + self.config.language)
            
        except Exception as e:
            logger.error(f"Error updating window text: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _initialize_default_profiles(self) -> None:
        """Initialize default power and refresh rate profiles at startup."""
        try:
            # Set Balanced power profile
            self._set_power_profile_sync("Balanced")
            
            # Set Auto refresh rate
            self._set_refresh_rate_sync("Auto")
            
            logger.info("Default profiles initialized: Balanced power profile and Auto refresh rate")
            
            # Update window text with current language
            self._update_window_text()
            
        except Exception as e:
            logger.error(f"Error initializing default profiles: {e}")

    def _on_language_change(self, value: str) -> None:
        """Handle language change."""
        try:
            # Update configuration
            self.config.language = value
            
            # Save configuration immediately
            self._save_config()
            
            # Update font when language changes
            self.current_font = load_custom_font(value)
            
            # Update all open windows
            for window in FrameworkControlCenter._open_windows:
                if window.winfo_exists():
                    if hasattr(window, 'current_font'):
                        window.current_font = self.current_font
                    if hasattr(window, '_update_window_text'):
                        window._update_window_text()
                    if hasattr(window, '_update_widgets_font'):
                        window._update_widgets_font()
                else:
                    FrameworkControlCenter._open_windows.remove(window)
                    
            logger.info(f"Language changed to: {value}")
            
        except Exception as e:
            logger.error(f"Error changing language: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _update_widgets_font(self) -> None:
        """Update font in all widgets."""
        def update_widget_font(widget):
            try:
                if hasattr(widget, 'cget') and 'font' in widget.keys():
                    current_font = widget.cget('font')
                    if isinstance(current_font, tuple):
                        # Keep the current font size if it's explicitly set
                        size = current_font[1]
                        weight = current_font[2] if len(current_font) > 2 else "normal"
                    else:
                        # Use the default size from the custom font
                        if isinstance(self.current_font, tuple):
                            size = self.current_font[1]
                        else:
                            size = 10
                        weight = "normal"
                    
                    # Use the font name from current_font
                    font_name = self.current_font[0] if isinstance(self.current_font, tuple) else self.current_font
                    widget.configure(font=(font_name, size))
                
                # Recursively update child widgets
                for child in widget.winfo_children():
                    update_widget_font(child)
            except Exception as e:
                logger.error(f"Error updating font for widget: {e}")

        update_widget_font(self)

    def _create_settings_window(self) -> None:
        """Create settings window."""
        if hasattr(self, 'settings_window') and self.settings_window.winfo_exists():
            self.settings_window.focus()
            return

        self.settings_window = ctk.CTkToplevel(self)
        self.settings_window.title(get_text(self.config.language, "settings_title"))
        self.settings_window.geometry("400x500")
        self.settings_window.resizable(False, False)
        
        # Configure window style
        self.settings_window.configure(fg_color=self.colors.background.main)
        
        try:
            if sys.platform.startswith('win'):
                self.settings_window.after(200, lambda: self.settings_window.iconbitmap(str(Path("assets/logo.ico").absolute())))
            else:
                self.settings_window.iconbitmap(str(Path("assets/logo.ico")))
        except Exception as e:
            logger.error(f"Failed to set window icon: {e}")
        
        # Main container with dark background
        container = ctk.CTkFrame(self.settings_window, fg_color=self.colors.background.main)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        # Theme selection
        theme_label = ctk.CTkLabel(
            container,
            text=get_text(self.config.language, "theme"),
            text_color=self.colors.text.primary,
            font=("Roboto", 11)
        )
        theme_label.pack(anchor="w", pady=(0, 5))
        
        # Get available themes
        themes = []
        try:
            for theme_file in Path("configs").glob("*_theme.json"):
                with open(theme_file, encoding="utf-8") as f:
                    theme_data = json.load(f)
                    themes.append(theme_data["name"])
        except Exception as e:
            logger.error(f"Error loading themes: {e}")
            themes = ["Default Dark", "Light Theme"]

        theme_var = ctk.StringVar(value=self.config.load_theme().name)
        theme_menu = ctk.CTkOptionMenu(
            container,
            values=themes,
            variable=theme_var,
            command=self._on_theme_change,
            fg_color=self.colors.background.secondary,
            button_color=self.colors.button.primary,
            button_hover_color=self.colors.hover,
            text_color=self.colors.text.primary
        )
        theme_menu.pack(fill="x", pady=(0, 15))

        # Language selection
        lang_label = ctk.CTkLabel(
            container,
            text=get_text(self.config.language, "language"),
            text_color=self.colors.text.primary,
            font=("Roboto", 11)
        )
        lang_label.pack(anchor="w", pady=(0, 5))
        
        lang_var = ctk.StringVar(value=self.config.language)
        language_menu = ctk.CTkOptionMenu(
            container,
            values=list(language_names.keys()),
            variable=lang_var,
            command=self._on_language_change,
            fg_color=self.colors.background.secondary,
            button_color=self.colors.button.primary,
            button_hover_color=self.colors.hover,
            text_color=self.colors.text.primary
        )
        language_menu.pack(fill="x", pady=(0, 15))

        # Minimize to tray option
        minimize_var = ctk.BooleanVar(value=self.config.minimize_to_tray)
        minimize_check = ctk.CTkCheckBox(
            container,
            text=get_text(self.config.language, "minimize_to_tray"),
            variable=minimize_var,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            border_color=self.colors.border.inactive,
            corner_radius=6
        )
        minimize_check.pack(anchor="w", pady=(0, 10))

        # Start minimized option
        start_min_var = ctk.BooleanVar(value=self.config.start_minimized)
        start_min_check = ctk.CTkCheckBox(
            container,
            text=get_text(self.config.language, "start_minimized"),
            variable=start_min_var,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            border_color=self.colors.border.inactive,
            corner_radius=6
        )
        start_min_check.pack(anchor="w", pady=(0, 10))

        # Start with Windows option
        start_windows_var = ctk.BooleanVar(value=self.config.start_with_windows)
        start_windows_check = ctk.CTkCheckBox(
            container,
            text=get_text(self.config.language, "start_with_windows"),
            variable=start_windows_var,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            border_color=self.colors.border.inactive,
            corner_radius=6
        )
        start_windows_check.pack(anchor="w", pady=(0, 15))

        # Monitoring interval
        interval_label = ctk.CTkLabel(
            container,
            text=get_text(self.config.language, "monitoring_interval"),
            text_color=self.colors.text.primary,
            font=("Roboto", 11)
        )
        interval_label.pack(anchor="w", pady=(0, 5))
        
        interval_var = ctk.StringVar(value=str(self.config.monitoring_interval))
        interval_entry = ctk.CTkEntry(
            container,
            textvariable=interval_var,
            fg_color=self.colors.background.secondary,
            text_color=self.colors.text.primary,
            border_color=self.colors.border.inactive
        )
        interval_entry.pack(fill="x", pady=(0, 15))

        # Save window position button
        save_pos_btn = ctk.CTkButton(
            container,
            text="Save current window position",
            command=self._save_window_position,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            height=35,
            corner_radius=6
        )
        save_pos_btn.pack(fill="x", pady=(0, 15))
        
        # Save button
        save_btn = ctk.CTkButton(
            container,
            text=get_text(self.config.language, "save"),
            command=lambda: self._save_settings(
                theme_var.get(),
                lang_var.get(),
                minimize_var.get(),
                start_min_var.get(),
                start_windows_var.get(),
                interval_var.get()
            ),
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            height=35,
            corner_radius=6
        )
        save_btn.pack(fill="x", pady=(0, 15))

    def _save_settings(self, theme: str, language: str, minimize_to_tray: bool,
                      start_minimized: bool, start_with_windows: bool, monitoring_interval: str) -> None:
        """Save settings to config file."""
        try:
            # Find theme file by name
            theme_file = None
            for theme_path in Path("configs").glob("*_theme.json"):
                with open(theme_path, encoding="utf-8") as f:
                    theme_data = json.load(f)
                    if theme_data["name"] == theme:
                        theme_file = theme_path.stem
                        break
            
            if theme_file:
                # Update config values
                self.config.current_theme = theme_file
                self.config.language = language
                self.config.minimize_to_tray = minimize_to_tray
                self.config.start_minimized = start_minimized
                self.config.start_with_windows = start_with_windows
                
                # Convert and validate monitoring interval
                try:
                    interval = int(monitoring_interval)
                    if interval < 100:  # Minimum 100ms
                        interval = 100
                    elif interval > 10000:  # Maximum 10 seconds
                        interval = 10000
                    
                    # Store old interval for comparison
                    old_interval = self.config.monitoring_interval
                    self.config.monitoring_interval = interval
                    
                    # Restart metrics update if interval changed
                    if old_interval != interval:
                        self._restart_metrics_update()
                        
                except ValueError:
                    logger.error(f"Invalid monitoring interval: {monitoring_interval}")
                    self.config.monitoring_interval = 1000  # Default to 1 second

                # Save to file
                self._save_config()
                
                # Apply settings immediately
                if minimize_to_tray and not hasattr(self, '_tray_icon'):
                    self._setup_tray()
                elif not minimize_to_tray and hasattr(self, '_tray_icon'):
                    self._tray_icon.stop()

                # Load and apply new theme
                theme = self.config.load_theme()
                self.colors = theme.colors
                self._update_window_colors()

                # Update language
                self.current_font = load_custom_font(language)
                self._update_widgets_font()
                self._update_window_text()

                logger.info("Settings saved and applied successfully")
                messagebox.showinfo(
                    get_text(self.config.language, "success"),
                    get_text(self.config.language, "settings_saved")
                )

            else:
                logger.error("Theme file not found")
                messagebox.showerror(
                    get_text(self.config.language, "error"),
                    get_text(self.config.language, "theme_not_found")
                )
                
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            messagebox.showerror(
                get_text(self.config.language, "error"),
                get_text(self.config.language, "settings_save_error")
            )

    def _update_theme(self, value: str) -> None:
        """Handle theme change."""
        self.config.theme = value
        ctk.set_appearance_mode(value)

    def _update_minimize_to_tray(self, value: bool) -> None:
        """Handle minimize to tray change."""
        self.config.minimize_to_tray = value

    def _update_start_minimized(self, value: bool) -> None:
        """Handle start minimized change."""
        self.config.start_minimized = value

    def _update_start_with_windows(self, value: bool) -> None:
        """Handle start with Windows change."""
        try:
            if value:
                # Get the path to the executable
                if getattr(sys, 'frozen', False):
                    # Running as compiled executable
                    exe_path = sys.executable
                else:
                    # Running as script
                    exe_path = os.path.abspath(sys.argv[0])

                success = self._create_startup_shortcut(exe_path)
            else:
                success = self._remove_startup_shortcut()

            if success:
                self.config.start_with_windows = value
                self._save_config()
                logger.info(f"Start with Windows {'enabled' if value else 'disabled'}")
            else:
                logger.error("Failed to update start with Windows setting")
                # Revert checkbox if operation failed
                if hasattr(self, 'settings_window') and self.settings_window.winfo_exists():
                    for widget in self.settings_window.winfo_children():
                        if isinstance(widget, ctk.CTkCheckBox) and widget.cget("text") == get_text("start_with_windows", self.config.language):
                            widget.deselect() if value else widget.select()
        except Exception as e:
            logger.error(f"Error updating start with Windows setting: {e}")

    def _check_log_file_size(self) -> None:
        """Periodically check and rotate log file if needed."""
        try:
            log_file = Path("logs") / f"{datetime.now().strftime('%Y-%m-%d')}.log"
            check_and_rotate_log(log_file)
        except Exception as e:
            logger.error(f"Error checking log file size: {e}")
        finally:
            # Schedule next check in 5 minutes
            self.after(300000, self._check_log_file_size)

    def _save_config(self) -> None:
        """Save configuration to file."""
        try:
            # Ensure config directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save configuration
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config.dict(), f, indent=4)
                
            logger.debug(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _on_theme_change(self, theme_name: str) -> None:
        """Handle theme change."""
        try:
            # Find theme file by name
            theme_file = None
            for theme_path in Path("configs").glob("*_theme.json"):
                with open(theme_path, encoding="utf-8") as f:
                    theme_data = json.load(f)
                    if theme_data["name"] == theme_name:
                        theme_file = theme_path.stem
                        break
            
            if theme_file:
                # Update configuration
                self.config.current_theme = theme_file
                
                # Save configuration
                self._save_config()
                
                # Load and apply new theme
                theme = self.config.load_theme()
                self.colors = theme.colors
                self.theme_fonts = theme.fonts
                self.spacing = theme.spacing
                self.radius = theme.radius
                
                # Update all windows
                for window in FrameworkControlCenter._open_windows:
                    if window.winfo_exists():
                        if hasattr(window, 'colors'):
                            window.colors = self.colors
                        window._update_window_colors()
                    else:
                        FrameworkControlCenter._open_windows.remove(window)
                
                logger.info(f"Theme changed to: {theme_name}")
            
        except Exception as e:
            logger.error(f"Error changing theme: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _update_window_colors(self) -> None:
        """Update colors in all widgets."""
        def update_widget_colors(widget):
            try:
                # Update background color
                if hasattr(widget, 'configure') and 'fg_color' in widget.keys():
                    current_color = widget.cget('fg_color')
                    if current_color == "#1E1E1E":  # Old main background
                        widget.configure(fg_color=self.colors.background.main)
                    elif current_color == "#2D2D2D":  # Old secondary background
                        widget.configure(fg_color=self.colors.background.secondary)
                    elif current_color == "#FF7043":  # Old primary color
                        widget.configure(fg_color=self.colors.button.primary)

                # Update text color
                if hasattr(widget, 'configure') and 'text_color' in widget.keys():
                    widget.configure(text_color=self.colors.text.primary)

                # Update button colors
                if isinstance(widget, ctk.CTkButton):
                    if widget.cget('text') == "×":  # Close button
                        widget.configure(
                            fg_color=self.colors.button.danger,
                            hover_color=self.colors.status.error,
                            text_color=self.colors.text.primary
                        )
                    else:
                        widget.configure(
                            fg_color=self.colors.button.primary,
                            hover_color=self.colors.hover,
                            text_color=self.colors.text.primary
                        )

                # Update progress bars
                if isinstance(widget, ctk.CTkProgressBar):
                    parent_text = None
                    if hasattr(widget, 'master'):
                        for child in widget.master.winfo_children():
                            if isinstance(child, ctk.CTkLabel):
                                parent_text = child.cget('text').lower()
                                break
                    
                    progress_color = self.colors.progress.bar
                    if parent_text:
                        if "cpu" in parent_text:
                            progress_color = self.colors.progress.cpu
                        elif "gpu" in parent_text or "igpu" in parent_text or "dgpu" in parent_text:
                            progress_color = self.colors.progress.gpu
                        elif "ram" in parent_text:
                            progress_color = self.colors.progress.ram
                        elif "temp" in parent_text:
                            progress_color = self.colors.progress.temp

                    widget.configure(
                        progress_color=progress_color,
                        fg_color=self.colors.progress.background
                    )

                # Update borders
                if hasattr(widget, 'configure') and 'border_color' in widget.keys():
                    if widget.cget('border_color') == "#FFFFFF":  # Old active border
                        widget.configure(border_color=self.colors.border.active)
                    else:
                        widget.configure(border_color=self.colors.border.inactive)

                # Update font sizes
                if hasattr(widget, 'configure') and 'font' in widget.keys():
                    current_font = widget.cget('font')
                    if isinstance(current_font, tuple):
                        family = current_font[0]
                        # Determine size based on context
                        if isinstance(widget, ctk.CTkLabel) and widget.master and isinstance(widget.master, ctk.CTkFrame):
                            if "header" in str(widget.master):
                                size = self.theme_fonts.main.size.title
                            else:
                                size = self.theme_fonts.main.size.normal
                        else:
                            size = self.theme_fonts.main.size.normal
                        widget.configure(font=(family, size))

                # Recursively update child widgets
                for child in widget.winfo_children():
                    update_widget_colors(child)
            except Exception as e:
                logger.error(f"Error updating colors for widget: {e}")

        # Update main window
        self.configure(fg_color=self.colors.background.main)
        update_widget_colors(self)

    def _minimize_to_tray(self) -> None:
        """Minimize window to system tray."""
        self.withdraw()

    def _toggle_window(self) -> None:
        """Toggle window visibility."""
        if self.winfo_viewable():
            self.withdraw()
            with self._tray_lock:
                if self.tray_icon is not None:
                    try:
                        self.tray_icon.notify(
                            title="Framework Control Center",
                            message="Application minimized to tray"
                        )
                    except Exception as e:
                        logger.error(f"Error showing tray notification: {e}")
        else:
            self.deiconify()
            self.lift()

    def on_closing(self) -> None:
        """Handle window closing."""
        try:
            # Cancel any pending metrics update
            if hasattr(self, '_metrics_after_id'):
                self.after_cancel(self._metrics_after_id)
                delattr(self, '_metrics_after_id')

            # Close event loop
            if hasattr(self, 'loop'):
                self.loop.close()
                delattr(self, 'loop')

            # Destroy window
            self.destroy()
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            self.destroy()


class UpdatesManager(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        FrameworkControlCenter._open_windows.append(self)  # Add window to list
        self.parent = parent
        self.title(get_text(self.parent.config.language, "updates_title"))
        self.geometry("800x600")
        self.colors = parent.colors
        self.current_font = load_custom_font(self.parent.config.language)
        
        # Configurer la couleur de fond de la fenêtre
        self.configure(fg_color=self.colors.background.main)
        
        # Configurer l'icône
        try:
            if sys.platform.startswith('win'):
                self.after(200, lambda: self.iconbitmap(str(Path("assets/logo.ico").absolute())))
            else:
                self.iconbitmap(str(Path("assets/logo.ico")))
        except Exception as e:
            logger.error(f"Failed to set window icon: {e}")
        
        # Initialiser les variables
        self.packages = {
            'winget': []
        }
        
        # Créer l'interface
        self._create_widgets()
        
        # Rendre la fenêtre modale
        self.transient(parent)
        self.grab_set()
        self.focus_set()
        
        # Gérer la fermeture de la fenêtre
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_widgets(self) -> None:
        """Create updates manager widgets."""
        # Main container
        container = ctk.CTkFrame(self, fg_color=self.colors.background.main)
        container.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Top frame pour les boutons de drivers
        top_frame = ctk.CTkFrame(container, fg_color=self.colors.background.main)
        top_frame.pack(fill="x", pady=(0, 10))
        
        # Label "Drivers"
        drivers_label = ctk.CTkLabel(
            top_frame,
            text="Drivers & BIOS",
            text_color=self.colors.text.primary,
            font=("Roboto", 12, "bold")
        )
        drivers_label.pack(side="left", padx=5)
        
        # Frame pour les boutons de drivers (alignés à droite)
        drivers_buttons = ctk.CTkFrame(top_frame, fg_color="transparent")
        drivers_buttons.pack(side="right")
        
        # Bouton Framework Drivers
        framework_btn = ctk.CTkButton(
            drivers_buttons,
            text="Framework Drivers",
            command=lambda: webbrowser.open("https://knowledgebase.frame.work/en_us/bios-and-drivers-downloads-rJ3PaCexh"),
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            height=32,
            width=150
        )
        framework_btn.pack(side="left", padx=5)
        
        # Bouton AMD Drivers
        amd_btn = ctk.CTkButton(
            drivers_buttons,
            text="AMD Drivers",
            command=lambda: webbrowser.open("https://www.amd.com/en/support/download/drivers.html"),
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            height=32,
            width=150
        )
        amd_btn.pack(side="left", padx=5)
        
        # Separator
        separator = ctk.CTkFrame(container, height=2, fg_color=self.colors.button.primary)
        separator.pack(fill="x", pady=10)
        
        # Frame principal pour la liste des paquets
        main_frame = ctk.CTkFrame(container, fg_color=self.colors.background.main)
        main_frame.pack(fill="both", expand=True, padx=5, pady=5)
        main_frame.grid_columnconfigure(0, weight=70)
        main_frame.grid_columnconfigure(1, weight=30)
        
        # Frame pour la liste des paquets (70% de la largeur)
        packages_frame = ctk.CTkFrame(main_frame, fg_color=self.colors.background.main)
        packages_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        # Label pour la section des paquets avec un style amélioré
        packages_header = ctk.CTkFrame(packages_frame, fg_color=self.colors.button.primary, height=40)
        packages_header.pack(fill="x", pady=(0, 10))
        packages_header.pack_propagate(False)
        
        ctk.CTkLabel(
            packages_header,
            text="System Packages",
            text_color=self.colors.text.primary,
            font=("Roboto", 12, "bold")
        ).pack(side="left", padx=10, pady=5)
        
        # En-tête des colonnes avec un style amélioré
        header_frame = ctk.CTkFrame(packages_frame, fg_color=self.colors.background.secondary, height=35)
        header_frame.pack(fill="x", padx=5, pady=(0, 5))
        header_frame.pack_propagate(False)
        header_frame.grid_columnconfigure(0, weight=0)  # Checkbox
        header_frame.grid_columnconfigure(1, weight=2)  # Nom
        header_frame.grid_columnconfigure(2, weight=1)  # Version actuelle
        header_frame.grid_columnconfigure(3, weight=1)  # Nouvelle version
        
        # Checkbox "Select All"
        self.select_all_var = ctk.BooleanVar()
        select_all = ctk.CTkCheckBox(
            header_frame,
            text="",
            variable=self.select_all_var,
            command=self._toggle_all_packages,
            width=20,
            height=20,
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=5,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            border_color=self.colors.border.inactive
        )
        select_all.grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Nom
        ctk.CTkLabel(
            header_frame,
            text="Name",
            text_color=self.colors.text.primary,
            font=("Roboto", 11, "bold"),
            anchor="w",
            width=200
        ).grid(row=0, column=1, sticky="w", padx=10, pady=5)
        
        # Version actuelle
        ctk.CTkLabel(
            header_frame,
            text="Current",
            text_color=self.colors.text.primary,
            font=("Roboto", 11, "bold"),
            anchor="e",
            width=100
        ).grid(row=0, column=2, sticky="e", padx=10, pady=5)
        
        # Nouvelle version
        ctk.CTkLabel(
            header_frame,
            text="Available",
            text_color=self.colors.text.primary,
            font=("Roboto", 11, "bold"),
            anchor="e",
            width=100
        ).grid(row=0, column=3, sticky="e", padx=10, pady=5)
        
        # Liste des paquets avec scrollbar et style amélioré
        self.packages_list = ctk.CTkScrollableFrame(
            packages_frame,
            fg_color=self.colors.background.secondary,
            label_text="",
            label_fg_color=self.colors.background.main,
            corner_radius=10
        )
        self.packages_list.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Frame pour les logs (30% de la largeur)
        logs_frame = ctk.CTkFrame(main_frame, fg_color=self.colors.background.main)
        logs_frame.grid(row=0, column=1, sticky="nsew")
        
        # En-tête des logs avec style amélioré
        logs_header = ctk.CTkFrame(logs_frame, fg_color=self.colors.button.primary, height=40)
        logs_header.pack(fill="x", pady=(0, 10))
        logs_header.pack_propagate(False)
        
        ctk.CTkLabel(
            logs_header,
            text="Logs",
            text_color=self.colors.text.primary,
            font=("Roboto", 12, "bold")
        ).pack(side="left", padx=10, pady=5)
        
        # Zone de texte pour les logs avec style amélioré
        self.log_text = ctk.CTkTextbox(
            logs_frame,
            fg_color=self.colors.background.secondary,
            text_color=self.colors.text.primary,
            wrap="word",
            corner_radius=10
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Create bottom buttons frame
        bottom_buttons = ctk.CTkFrame(container, fg_color="transparent")
        bottom_buttons.pack(fill="x", pady=(10, 0))
        
        # Create Check Updates button
        check_button = ctk.CTkButton(
            bottom_buttons,
            text="Check installed apps",
            command=lambda: threading.Thread(target=self._check_updates, daemon=True).start(),
            height=35,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            corner_radius=10
        )
        check_button.pack(side="left", padx=5)
        
        # Create Update Selected button
        update_button = ctk.CTkButton(
            bottom_buttons,
            text="Update selection",
            command=self._update_selected,
            height=35,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            corner_radius=10
        )
        update_button.pack(side="left", padx=5)
        
        # Create Refresh List button
        refresh_button = ctk.CTkButton(
            bottom_buttons,
            text="Refresh List",
            command=lambda: threading.Thread(target=self._check_updates, daemon=True).start(),
            height=35,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            text_color=self.colors.text.primary,
            corner_radius=10
        )
        refresh_button.pack(side="left", padx=5)

    def _on_close(self):
        """Gérer la fermeture propre de la fenêtre."""
        try:
            FrameworkControlCenter._open_windows.remove(self)  # Remove window from list
            self.grab_release()
            self.destroy()
        except Exception as e:
            logger.error(f"Error closing Update Manager: {e}")
            self.destroy()

    def _check_updates(self) -> None:
        """Vérifier les mises à jour disponibles."""
        try:
            # Nettoyer la liste des paquets
            for widget in self.packages_list.winfo_children():
                widget.destroy()
            
            # Effacer les logs existants
            self.log_text.delete("1.0", "end")
            
            # Ajouter un message de démarrage dans les logs
            self._add_log("Checking for system updates...\n")
            
            self._check_winget_updates()
                
        except Exception as e:
            self._add_log(f"Error checking updates: {str(e)}\n")
            logger.error(f"Error checking updates: {e}")

    def _check_winget_updates(self) -> None:
        """Vérifier les mises à jour winget."""
        try:
            # Configure process to hide window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            # Liste des paquets installés
            process = subprocess.run(
                ["winget", "list", "--accept-source-agreements", "--disable-interactivity"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                startupinfo=startupinfo
            )
            
            if process.returncode != 0:
                raise ValueError(f"winget list failed: {process.stderr}")
            
            self._add_log("Scanning installed packages...\n")
            
            # Parser la sortie pour extraire les paquets installés
            installed = {}
            lines = process.stdout.split('\n')
            
            # Chercher la ligne d'en-tête (plusieurs formats possibles)
            header_index = -1
            for i, line in enumerate(lines):
                if any(all(col in line for col in combo) for combo in [
                    ["Name", "Id", "Version"],  # Format standard
                    ["Nom", "ID", "Version"],   # Format français
                    ["名称", "ID", "版"]      # Format autres langues
                ]):
                    header_index = i
                    break
            
            if header_index == -1:
                # Essayer une autre commande
                process = subprocess.run(
                    ["winget", "list", "--source", "winget", "--accept-source-agreements"],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    startupinfo=startupinfo
                )
                lines = process.stdout.split('\n')
                for i, line in enumerate(lines):
                    if any(all(col in line for col in combo) for combo in [
                        ["Name", "Id", "Version"],
                        ["Nom", "ID", "Version"],
                        ["名称", "ID", "版本"]
                    ]):
                        header_index = i
                        break
            
            if header_index == -1:
                self._add_log("Warning: Could not parse winget list output format\n")
                self._add_log("Raw output:\n" + process.stdout + "\n")
                return
            
            # Extraire les positions des colonnes
            header = lines[header_index]
            # Chercher les colonnes dans différentes langues
            name_pos = max(header.find("Name"), header.find("Nom"), header.find("名称"))
            id_pos = max(header.find("Id"), header.find("ID"), header.find("标识符"))
            version_pos = max(header.find("Version"), header.find("版本"))
            
            # Parser les paquets installés
            for line in lines[header_index + 2:]:  # Skip header and separator
                if line.strip() and not line.startswith("-"):
                    try:
                        if len(line) > version_pos:
                            name = line[name_pos:id_pos].strip()
                            version = line[version_pos:].strip().split()[0]
                            if name and version:
                                installed[name] = version
                    except Exception as e:
                        logger.debug(f"Failed to parse line: {line}, error: {e}")
                        continue
            
            self._add_log(f"Found {len(installed)} installed packages\n")
            self._add_log("Checking for updates...\n")
            
            # Vérifier les mises à jour disponibles
            process = subprocess.run(
                ["winget", "upgrade", "--include-unknown", "--disable-interactivity", "--accept-source-agreements"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                startupinfo=startupinfo
            )
            
            if process.returncode != 0:
                raise ValueError(f"winget upgrade check failed: {process.stderr}")
            
            # Parser la sortie des mises à jour
            updates = {}
            lines = process.stdout.split('\n')
            
            # Chercher la ligne d'en-tête
            header_index = -1
            for i, line in enumerate(lines):
                if any(all(col in line for col in combo) for combo in [
                    ["Name", "Version", "Available"],
                    ["Nom", "Version", "Disponible"],
                    ["名称", "版本", "可用"]
                ]):
                    header_index = i
                    break
            
            if header_index != -1:
                # Extraire les positions des colonnes
                header = lines[header_index]
                name_pos = max(header.find("Name"), header.find("Nom"), header.find("名称"))
                version_pos = header.find("Version")
                available_pos = max(header.find("Available"), header.find("Disponible"), header.find("可用"))
                
                # Parser les mises à jour disponibles
                for line in lines[header_index + 2:]:  # Skip header and separator
                    if line.strip() and not line.startswith("-"):
                        try:
                            if len(line) > available_pos:
                                name = line[name_pos:version_pos].strip()
                                current = line[version_pos:available_pos].strip()
                                new = line[available_pos:].strip()
                                if name and current and new:
                                    updates[name] = (current, new)
                        except Exception as e:
                            logger.debug(f"Failed to parse update line: {line}, error: {e}")
                            continue
            
            # Afficher d'abord les paquets avec des mises à jour
            for name, (current, new) in updates.items():
                self._add_package_to_list(name, current, new)
                self._add_log(f"Update available: {name} ({current} → {new})\n")
            
            # Puis afficher les autres paquets installés
            for name, version in installed.items():
                if name not in updates:
                    self._add_package_to_list(name, version, None)
            
            self._add_log(f"\nFound {len(updates)} updates available.\n")
            
        except Exception as e:
            self._add_log(f"Error: {str(e)}\n")
            logger.error(f"Error checking winget updates: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")

    def _add_package_to_list(
        self,
        name: str,
        current_version: str,
        new_version: Optional[str]
    ) -> None:
        """Ajouter un paquet à la liste avec son statut de mise à jour."""
        # Frame pour le paquet avec fond transparent
        package_frame = ctk.CTkFrame(self.packages_list, fg_color="transparent")
        package_frame.pack(fill="x", padx=5, pady=2)
        package_frame.grid_columnconfigure(0, weight=0)  # Checkbox
        package_frame.grid_columnconfigure(1, weight=2)  # Nom (plus large)
        package_frame.grid_columnconfigure(2, weight=1)  # Version actuelle
        package_frame.grid_columnconfigure(3, weight=1)  # Flèche et nouvelle version
        
        # Checkbox pour la sélection (colonne 0)
        checkbox_var = ctk.BooleanVar()
        checkbox = ctk.CTkCheckBox(
            package_frame,
            text="",
            variable=checkbox_var,
            width=20,
            height=20,
            checkbox_width=20,
            checkbox_height=20,
            corner_radius=5,
            fg_color=self.colors.button.primary,
            hover_color=self.colors.hover,
            border_color=self.colors.border.inactive
        )
        checkbox.grid(row=0, column=0, sticky="w", padx=5)
        
        # Stocker la référence à la checkbox dans le frame
        package_frame.checkbox = checkbox_var
        package_frame.package_name = name
        
        # Nom du paquet (colonne 1)
        name_label = ctk.CTkLabel(
            package_frame,
            text=name,
            text_color=self.colors.text.primary,
            anchor="w",
            width=200  # Largeur fixe pour le nom
        )
        name_label.grid(row=0, column=1, sticky="w", padx=5)
        
        # Version actuelle (colonne 2)
        current_label = ctk.CTkLabel(
            package_frame,
            text=current_version,
            text_color=self.colors.text.primary,
            anchor="e",
            width=100  # Largeur fixe pour la version
        )
        current_label.grid(row=0, column=2, sticky="e", padx=5)
        
        # Nouvelle version (colonne 3)
        if new_version:
            version_frame = ctk.CTkFrame(package_frame, fg_color="transparent")
            version_frame.grid(row=0, column=3, sticky="e", padx=5)
            
            # Flèche
            arrow_label = ctk.CTkLabel(
                version_frame,
                text="→",
                text_color=self.colors.status.success,  # Vert
                anchor="e"
            )
            arrow_label.pack(side="left", padx=2)
            
            # Nouvelle version
            update_label = ctk.CTkLabel(
                version_frame,
                text=new_version,
                text_color=self.colors.status.success,  # Vert
                anchor="e",
                width=100  # Largeur fixe pour la version
            )
            update_label.pack(side="left", padx=2)
            
            # Activer la checkbox seulement si une mise à jour est disponible
            checkbox.configure(state="normal")
        else:
            checkbox.configure(state="disabled")

    def _add_log(self, message: str) -> None:
        """Add message to log window."""
        try:
            if hasattr(self, 'log_text'):
                self.log_text.configure(state="normal")
                self.log_text.insert("end", message)
                self.log_text.configure(state="disabled")
                self.log_text.see("end")
                self.update_idletasks()
        except Exception as e:
            logger.error(f"Error adding log message: {e}")

    def _update_selected(self) -> None:
        """Mettre à jour les paquets sélectionnés."""
        # Lancer la mise à jour en arrière-plan
        threading.Thread(
            target=self._update_selected_thread,
            daemon=True
        ).start()

    def _update_selected_thread(self) -> None:
        """Thread pour mettre à jour les paquets sélectionnés."""
        try:
            # Configure process to hide window
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            
            # Récupérer les paquets sélectionnés
            selected_packages = []
            for widget in self.packages_list.winfo_children():
                if hasattr(widget, 'checkbox') and widget.checkbox.get():
                    selected_packages.append(widget.package_name)
            
            if not selected_packages:
                self._add_log("\nNo packages selected for update.\n")
                return
            
            self._add_log(f"\nUpdating {len(selected_packages)} selected packages...\n")
            
            # Mettre à jour chaque paquet sélectionné
            for package in selected_packages:
                self._add_log(f"\nUpdating {package}...\n")
                process = subprocess.run(
                    ["winget", "upgrade", package],
                    capture_output=True,
                    text=True,
                    startupinfo=startupinfo
                )
                self._add_log(process.stdout)
                
            self._add_log("\nUpdate process completed.\n")
            
            # Rafraîchir la liste
            self._check_updates()
            
        except Exception as e:
            self._add_log(f"Error during update: {str(e)}\n")
            logger.error(f"Error updating packages: {e}")

    def _toggle_all_packages(self) -> None:
        """Sélectionner/désélectionner tous les paquets."""
        selected = self.select_all_var.get()
        for widget in self.packages_list.winfo_children():
            if hasattr(widget, 'checkbox'):
                checkbox = widget.winfo_children()[0]  # La checkbox est le premier enfant
                if checkbox.cget("state") == "normal":  # Ne changer que les checkboxes actives
                    widget.checkbox.set(selected)


def main():
    """Main function."""
    # ... existing code ...
