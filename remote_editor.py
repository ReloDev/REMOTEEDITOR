import os
import queue
import threading
import uuid
import tkinter as tk

from pathlib import Path, PurePosixPath
from datetime import datetime
from tkinter import ttk, filedialog, messagebox

import paramiko


APP_TITLE = "Éditeur SSH"
MAX_FILE_BYTES = 2 * 1024 * 1024


class SSHFileEditor(tk.Tk):
   

    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1300x780")
        self.minsize(1000, 620)

        self.client = None
        self.sftp = None

        self.loaded_path = None
        self.loaded_mode = None
        self.loaded_mtime = None

        self.original_content = ""
        self.file_encoding = "utf-8"
        self.file_has_bom = False
        self.dirty = False

        self.busy = False
        self.events = queue.Queue()
        self.setting_text = False

        # Connexion
        self.host_var = tk.StringVar()
        self.user_var = tk.StringVar(value="root")
        self.port_var = tk.StringVar(value="22")

        self.auth_var = tk.StringVar(value="key")
        self.password_var = tk.StringVar()
        self.key_var = tk.StringVar(
            value=str(Path.home() / ".ssh" / "id_ed25519")
        )

        # Fichier
        self.remote_path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Déconnecté")

        # Recherche
        self.search_var = tk.StringVar()
        self.case_sensitive_var = tk.BooleanVar(value=False)

        self.search_matches = []
        self.current_match = -1

        self._build_ui()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._process_events)

    # ==================================================
    # INTERFACE
    # ==================================================

    def _build_ui(self):

        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        main.columnconfigure(0, weight=1, minsize=290)
        main.columnconfigure(1, weight=3, minsize=550)
        main.rowconfigure(0, weight=1)

        # --------------------------------------------------
        # COLONNE GAUCHE : CONNEXION
        # --------------------------------------------------

        left = ttk.Frame(main)
        left.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=(0, 12)
        )

        connection = ttk.LabelFrame(
            left,
            text="Connexion au serveur",
            padding=12
        )
        connection.pack(fill="x")

        connection.columnconfigure(0, weight=1)

        ttk.Label(
            connection,
            text="Adresse IP ou hôte"
        ).grid(row=0, column=0, sticky="w")

        ttk.Entry(
            connection,
            textvariable=self.host_var
        ).grid(
            row=1, column=0, sticky="ew", pady=(3, 10)
        )

        ttk.Label(
            connection,
            text="Nom d'utilisateur"
        ).grid(row=2, column=0, sticky="w")

        ttk.Entry(
            connection,
            textvariable=self.user_var
        ).grid(
            row=3, column=0, sticky="ew", pady=(3, 10)
        )

        ttk.Label(
            connection,
            text="Port SSH"
        ).grid(row=4, column=0, sticky="w")

        ttk.Entry(
            connection,
            textvariable=self.port_var
        ).grid(
            row=5, column=0, sticky="ew", pady=(3, 10)
        )

        ttk.Separator(connection).grid(
            row=6,
            column=0,
            sticky="ew",
            pady=8
        )

        ttk.Label(
            connection,
            text="Méthode d'authentification"
        ).grid(row=7, column=0, sticky="w")

        ttk.Radiobutton(
            connection,
            text="Mot de passe",
            variable=self.auth_var,
            value="password",
            command=self._update_auth_fields
        ).grid(
            row=8, column=0, sticky="w", pady=(6, 0)
        )

        ttk.Radiobutton(
            connection,
            text="Clé privée SSH",
            variable=self.auth_var,
            value="key",
            command=self._update_auth_fields
        ).grid(
            row=9, column=0, sticky="w", pady=(4, 8)
        )

        self.password_label = ttk.Label(
            connection,
            text="Mot de passe"
        )

        self.password_entry = ttk.Entry(
            connection,
            textvariable=self.password_var,
            show="*"
        )

        self.key_label = ttk.Label(
            connection,
            text="Fichier de clé privée"
        )

        self.key_entry = ttk.Entry(
            connection,
            textvariable=self.key_var
        )

        self.key_button = ttk.Button(
            connection,
            text="Parcourir...",
            command=self._browse_key
        )

        self.password_label.grid(
            row=10, column=0, sticky="w"
        )

        self.password_entry.grid(
            row=11, column=0, sticky="ew", pady=(3, 8)
        )

        self.key_label.grid(
            row=12, column=0, sticky="w"
        )

        self.key_entry.grid(
            row=13, column=0, sticky="ew", pady=(3, 5)
        )

        self.key_button.grid(
            row=14, column=0, sticky="ew", pady=(0, 10)
        )

        self.connect_button = ttk.Button(
            connection,
            text="Se connecter",
            command=self.connect
        )
        self.connect_button.grid(
            row=15, column=0, sticky="ew", pady=(8, 4)
        )

        self.disconnect_button = ttk.Button(
            connection,
            text="Déconnecter",
            command=self.disconnect
        )
        self.disconnect_button.grid(
            row=16, column=0, sticky="ew"
        )

        info = ttk.LabelFrame(
            left,
            text="Informations",
            padding=10
        )
        info.pack(fill="x", pady=(12, 0))

        ttk.Label(
            info,
            text=(
                "• Fichiers texte uniquement\n"
                "• Taille maximale : 2 Mio\n"
                "• Sauvegarde avant remplacement\n"
                "• Permissions Linux respectées"
            ),
            justify="left",
            wraplength=250
        ).pack(anchor="w")

        # --------------------------------------------------
        # COLONNE DROITE : ÉDITEUR
        # --------------------------------------------------

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")

        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        ttk.Label(
            right,
            text="Éditeur de fichiers",
            font=("Segoe UI", 14, "bold")
        ).grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )

        # Chemin du fichier
        file_frame = ttk.LabelFrame(
            right,
            text="Fichier distant",
            padding=10
        )
        file_frame.grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(0, 8)
        )
        file_frame.columnconfigure(0, weight=1)

        ttk.Label(
            file_frame,
            text="Chemin absolu du fichier sur le serveur"
        ).grid(row=0, column=0, sticky="w")

        path_row = ttk.Frame(file_frame)
        path_row.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(4, 0)
        )
        path_row.columnconfigure(0, weight=1)

        ttk.Entry(
            path_row,
            textvariable=self.remote_path_var
        ).grid(row=0, column=0, sticky="ew")

        self.load_button = ttk.Button(
            path_row,
            text="Charger",
            command=self.load_file
        )
        self.load_button.grid(
            row=0, column=1, padx=(6, 0)
        )

        self.reload_button = ttk.Button(
            path_row,
            text="Recharger",
            command=self.reload_file
        )
        self.reload_button.grid(
            row=0, column=2, padx=(6, 0)
        )

        # Barre d'actions
        toolbar = ttk.Frame(right)
        toolbar.grid(
            row=2, column=0, sticky="ew", pady=(0, 8)
        )

        self.save_button = ttk.Button(
            toolbar,
            text="Enregistrer sur le serveur",
            command=self.save_file
        )
        self.save_button.pack(side="left")

        ttk.Button(
            toolbar,
            text="Ouvrir un fichier local",
            command=self.open_local_file
        ).pack(side="left", padx=(8, 0))

        ttk.Button(
            toolbar,
            text="Copie locale",
            command=self.save_local_copy
        ).pack(side="left", padx=(8, 0))

        # Barre de recherche
        search_frame = ttk.LabelFrame(
            right,
            text="Rechercher dans le fichier",
            padding=8
        )
        search_frame.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(0, 8)
        )
        search_frame.columnconfigure(0, weight=1)

        self.search_entry = ttk.Entry(
            search_frame,
            textvariable=self.search_var
        )
        self.search_entry.grid(
            row=0, column=0, sticky="ew", padx=(0, 6)
        )

        self.search_entry.bind(
            "<Return>",
            lambda event: self.find_next()
        )

        self.search_entry.bind(
            "<Shift-Return>",
            lambda event: self.find_previous()
        )

        self.search_entry.bind(
            "<KeyRelease>",
            self._on_search_key
        )

        ttk.Button(
            search_frame,
            text="Précédent",
            command=self.find_previous
        ).grid(row=0, column=1, padx=3)

        ttk.Button(
            search_frame,
            text="Suivant",
            command=self.find_next
        ).grid(row=0, column=2, padx=3)

        ttk.Checkbutton(
            search_frame,
            text="Respecter la casse",
            variable=self.case_sensitive_var,
            command=self.search_text
        ).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )

        self.search_count_label = ttk.Label(
            search_frame,
            text="0 occurrence"
        )
        self.search_count_label.grid(
            row=1, column=1, columnspan=2,
            sticky="e", pady=(6, 0)
        )

        # Zone d'édition
        editor_frame = ttk.Frame(right)
        editor_frame.grid(
            row=4, column=0, sticky="nsew"
        )

        right.rowconfigure(4, weight=1)

        self.editor = tk.Text(
            editor_frame,
            wrap="none",
            undo=True,
            font=("Consolas", 10),
            padx=8,
            pady=8
        )

        vertical_scroll = ttk.Scrollbar(
            editor_frame,
            orient="vertical",
            command=self.editor.yview
        )

        horizontal_scroll = ttk.Scrollbar(
            editor_frame,
            orient="horizontal",
            command=self.editor.xview
        )

        self.editor.configure(
            yscrollcommand=vertical_scroll.set,
            xscrollcommand=horizontal_scroll.set
        )

        self.editor.grid(
            row=0, column=0, sticky="nsew"
        )

        vertical_scroll.grid(
            row=0, column=1, sticky="ns"
        )

        horizontal_scroll.grid(
            row=1, column=0, sticky="ew"
        )

        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(0, weight=1)

        self.editor.bind(
            "<<Modified>>",
            self._on_modified
        )

        self.editor.tag_configure(
            "search_match",
            background="#FFE082",
            foreground="#202020"
        )

        self.editor.tag_configure(
            "current_match",
            background="#FF9800",
            foreground="#FFFFFF"
        )

        # Barre d'état
        ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=6
        ).pack(fill="x", side="bottom")

        self._update_auth_fields()

    # ==================================================
    # AUTHENTIFICATION
    # ==================================================

    def _update_auth_fields(self):

        password_mode = self.auth_var.get() == "password"

        self.password_entry.configure(
            state="normal" if password_mode else "disabled"
        )

        self.password_label.configure(
            state="normal" if password_mode else "disabled"
        )

        self.key_entry.configure(
            state="disabled" if password_mode else "normal"
        )

        self.key_label.configure(
            state="disabled" if password_mode else "normal"
        )

        self.key_button.configure(
            state="disabled" if password_mode else "normal"
        )

    def _browse_key(self):

        path = filedialog.askopenfilename(
            title="Sélectionner une clé privée SSH"
        )

        if path:
            self.key_var.set(path)

    # ==================================================
    # GESTION DES TÂCHES
    # ==================================================

    def _set_busy(self, busy, message=None):

        self.busy = busy
        state = "disabled" if busy else "normal"

        for button in (
            self.connect_button,
            self.disconnect_button,
            self.load_button,
            self.reload_button,
            self.save_button
        ):
            button.configure(state=state)

        if message:
            self.status_var.set(message)

    def _worker(self, task, *args):

        if self.busy:
            return

        self._set_busy(True, "Opération en cours...")

        threading.Thread(
            target=self._run_task,
            args=(task, args),
            daemon=True
        ).start()

    def _run_task(self, task, args):

        try:
            result = task(*args)
            self.events.put(
                ("success", task.__name__, result)
            )

        except Exception as exc:
            self.events.put((
                "error",
                task.__name__,
                f"{type(exc).__name__}: {exc}"
            ))

    def _process_events(self):

        try:
            while True:

                kind, task_name, result = (
                    self.events.get_nowait()
                )

                self._set_busy(False)

                if kind == "error":

                    self.status_var.set(
                        "Échec de l'opération"
                    )

                    messagebox.showerror(
                        "Erreur",
                        self._explain_error(result)
                    )
                    continue

                if task_name == "_connect_worker":

                    self.client, self.sftp = result

                    self.status_var.set(
                        f"Connecté à {self.host_var.get()}"
                    )

                    messagebox.showinfo(
                        "Connexion",
                        "Connexion SSH établie avec succès."
                    )

                elif task_name == "_disconnect_worker":

                    self.client = None
                    self.sftp = None

                    self.status_var.set("Déconnecté")

                elif task_name == "_load_worker":

                    self._apply_loaded_file(*result)

                elif task_name == "_save_worker":

                    content, new_mtime = result

                    self.original_content = content
                    self.loaded_mtime = new_mtime
                    self.dirty = False

                    self.search_text()

                    self.status_var.set(
                        f"Enregistré : {self.loaded_path}"
                    )

                    messagebox.showinfo(
                        "Enregistrement",
                        "Fichier enregistré avec succès.\n\n"
                        "Une copie de sauvegarde a été créée "
                        "sur le serveur."
                    )

                elif task_name == "_local_load_worker":

                    content, encoding, bom, path = result

                    self._set_editor_content(content)

                    self.file_encoding = encoding
                    self.file_has_bom = bom
                    self.loaded_path = None
                    self.loaded_mode = None
                    self.loaded_mtime = None

                    self.original_content = content
                    self.dirty = False

                    self.search_text()

                    self.status_var.set(
                        f"Fichier local ouvert : {path}"
                    )

        except queue.Empty:
            pass

        self.after(100, self._process_events)

    @staticmethod
    def _explain_error(error):

        text = str(error)
        lower = text.lower()

        if "permission denied" in lower:
            return (
                f"{text}\n\n"
                "Le compte SSH ne possède probablement pas les "
                "droits requis sur le fichier ou le répertoire. "
                "Vérifiez les permissions Linux."
            )

        if "file not open for writing" in lower:
            return (
                f"{text}\n\n"
                "Le serveur SFTP a refusé une opération d'écriture. "
                "Vérifiez les droits du compte SSH et les permissions "
                "du répertoire contenant le fichier."
            )

        if "authentication failed" in lower:
            return (
                f"{text}\n\n"
                "Vérifiez le nom d'utilisateur, le mot de passe "
                "ou la clé privée."
            )

        if "no such file" in lower:
            return (
                f"{text}\n\n"
                "Vérifiez le chemin du fichier distant."
            )

        return text

    # ==================================================
    # CONNEXION SSH
    # ==================================================

    def _connect_worker(
        self, host, username, port, auth, secret
    ):

        client = paramiko.SSHClient()
        client.load_system_host_keys()

        # Refuse les serveurs dont la clé d'hôte est inconnue.
        client.set_missing_host_key_policy(
            paramiko.RejectPolicy()
        )

        options = {
            "hostname": host,
            "username": username,
            "port": port,
            "timeout": 15,
            "banner_timeout": 15,
            "auth_timeout": 15,
            "look_for_keys": False,
            "allow_agent": True
        }

        if auth == "password":
            options["password"] = secret
        else:
            options["key_filename"] = secret

        try:
            client.connect(**options)
            sftp = client.open_sftp()

            return client, sftp

        except Exception:
            client.close()
            raise

    def connect(self):

        if self.busy:
            return

        host = self.host_var.get().strip()
        username = self.user_var.get().strip()

        try:
            port = int(self.port_var.get())

            if not host or not username:
                raise ValueError

            if not 1 <= port <= 65535:
                raise ValueError

        except ValueError:
            messagebox.showwarning(
                "Paramètres invalides",
                "Renseignez l'hôte, l'utilisateur et un port "
                "compris entre 1 et 65535."
            )
            return

        if self.client or self.sftp:
            messagebox.showwarning(
                "Connexion existante",
                "Déconnectez-vous avant de changer de serveur."
            )
            return

        auth = self.auth_var.get()

        if auth == "password":
            secret = self.password_var.get()

        else:
            secret = os.path.expanduser(
                self.key_var.get().strip()
            )

            if not os.path.isfile(secret):
                messagebox.showwarning(
                    "Clé introuvable",
                    "Le fichier de clé privée indiqué n'existe pas."
                )
                return

        if not secret:
            messagebox.showwarning(
                "Authentification",
                "Renseignez le mot de passe ou la clé privée."
            )
            return

        self._worker(
            self._connect_worker,
            host,
            username,
            port,
            auth,
            secret
        )

    def _disconnect_worker(self):

        if self.sftp:
            self.sftp.close()

        if self.client:
            self.client.close()

        return True

    def disconnect(self):

        if self.busy:
            messagebox.showwarning(
                "Opération en cours",
                "Attendez la fin de l'opération actuelle."
            )
            return

        if not self.client and not self.sftp:
            self.status_var.set("Déconnecté")
            return

        self._worker(self._disconnect_worker)

    # ==================================================
    # CHARGEMENT D'UN FICHIER DISTANT
    # ==================================================

    def load_file(self):

        if not self.sftp:
            messagebox.showwarning(
                "Connexion requise",
                "Connectez-vous d'abord au serveur."
            )
            return

        path = self.remote_path_var.get().strip()

        if not path.startswith("/"):
            messagebox.showwarning(
                "Chemin invalide",
                "Indiquez un chemin absolu, par exemple :\n"
                "/etc/ssh/sshd_config"
            )
            return

        if self.dirty:
            if not messagebox.askyesno(
                "Modifications non enregistrées",
                "Abandonner les modifications actuelles ?"
            ):
                return

        self._worker(self._load_worker, path)

    def _load_worker(self, path):

        stat = self.sftp.stat(path)

        if stat.st_size > MAX_FILE_BYTES:
            raise ValueError(
                f"Fichier trop volumineux : {stat.st_size} octets. "
                f"Limite : {MAX_FILE_BYTES} octets."
            )

        with self.sftp.open(path, "rb") as remote_file:
            raw = remote_file.read(MAX_FILE_BYTES + 1)

        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(
                "Le fichier dépasse la taille maximale autorisée."
            )

        if b"\x00" in raw:
            raise ValueError(
                "Le fichier semble binaire. "
                "Seuls les fichiers texte sont acceptés."
            )

        bom = raw.startswith(b"\xef\xbb\xbf")

        try:
            content = raw.decode(
                "utf-8-sig" if bom else "utf-8"
            )
            encoding = "utf-8"

        except UnicodeDecodeError:
            content = raw.decode("latin-1")
            encoding = "latin-1"

        return (
            content,
            encoding,
            bom,
            path,
            stat.st_mode,
            stat.st_mtime
        )

    def _apply_loaded_file(
        self, content, encoding, bom,
        path, mode, mtime
    ):

        self._set_editor_content(content)

        self.file_encoding = encoding
        self.file_has_bom = bom

        self.loaded_path = path
        self.loaded_mode = mode
        self.loaded_mtime = mtime

        self.remote_path_var.set(path)

        self.original_content = content
        self.dirty = False

        self.search_text()

        self.status_var.set(
            f"Fichier chargé : {path}"
        )

    def _set_editor_content(self, content):

        self.setting_text = True

        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", content)
        self.editor.edit_modified(False)

        self.setting_text = False

        self.search_matches = []
        self.current_match = -1

    # ==================================================
    # DÉTECTION DES MODIFICATIONS
    # ==================================================

    def _on_modified(self, _event=None):

        if not self.editor.edit_modified():
            return

        if not self.setting_text:

            current = self.editor.get("1.0", "end-1c")

            self.dirty = (
                current != self.original_content
            )

            marker = (
                " — modifications non enregistrées"
                if self.dirty else ""
            )

            if self.loaded_path:
                self.status_var.set(
                    f"{self.loaded_path}{marker}"
                )
            else:
                self.status_var.set(
                    f"Édition locale{marker}"
                )

            # Met à jour les résultats après une modification.
            self.search_text()

        self.editor.edit_modified(False)

    # ==================================================
    # RECHERCHE
    # ==================================================

    def _on_search_key(self, _event=None):
        self.search_text()

    def search_text(self):

        query = self.search_var.get()

        self.editor.tag_remove(
            "search_match", "1.0", "end"
        )
        self.editor.tag_remove(
            "current_match", "1.0", "end"
        )

        self.search_matches = []
        self.current_match = -1

        if not query:
            self.search_count_label.configure(
                text="0 occurrence"
            )
            return

        content = self.editor.get("1.0", "end-1c")

        if self.case_sensitive_var.get():
            source = content
            target = query
        else:
            source = content.casefold()
            target = query.casefold()

        position = 0

        while True:

            index = source.find(target, position)

            if index == -1:
                break

            start = f"1.0+{index}c"
            end = f"1.0+{index + len(query)}c"

            self.search_matches.append((start, end))

            self.editor.tag_add(
                "search_match", start, end
            )

            # Permet aussi de trouver des occurrences chevauchantes.
            position = index + 1

        count = len(self.search_matches)

        self.search_count_label.configure(
            text=f"{count} occurrence{'s' if count != 1 else ''}"
        )

        if count:
            self.current_match = 0
            self._select_match(0)

    def _select_match(self, index):

        if not self.search_matches:
            return

        self.current_match = index % len(self.search_matches)

        start, end = self.search_matches[self.current_match]

        self.editor.tag_remove(
            "current_match", "1.0", "end"
        )

        self.editor.tag_add(
            "current_match", start, end
        )

        self.editor.mark_set("insert", end)
        self.editor.see(start)

        self.search_count_label.configure(
            text=(
                f"{self.current_match + 1}/"
                f"{len(self.search_matches)} occurrence(s)"
            )
        )

    def find_next(self):

        if not self.search_matches:
            self.search_text()

            if not self.search_matches:
                return

            if len(self.search_matches) == 1:
                return

        self._select_match(self.current_match + 1)

    def find_previous(self):

        if not self.search_matches:
            self.search_text()

            if not self.search_matches:
                return

            if len(self.search_matches) == 1:
                return

        self._select_match(self.current_match - 1)

    # ==================================================
    # RECHARGEMENT
    # ==================================================

    def reload_file(self):

        if not self.loaded_path:
            self.load_file()
            return

        if self.dirty:
            if not messagebox.askyesno(
                "Recharger",
                "Abandonner les modifications non enregistrées ?"
            ):
                return

        self.remote_path_var.set(self.loaded_path)
        self.load_file()

    # ==================================================
    # ENREGISTREMENT DISTANT
    # ==================================================

    def save_file(self):

        if not self.client or not self.sftp:
            messagebox.showwarning(
                "Connexion requise",
                "Connectez-vous d'abord au serveur."
            )
            return

        if not self.loaded_path:
            messagebox.showwarning(
                "Aucun fichier distant",
                "Chargez un fichier distant avant de l'enregistrer."
            )
            return

        if not self.dirty:
            self.status_var.set(
                "Aucune modification à enregistrer."
            )
            return

        content = self.editor.get("1.0", "end-1c")

        confirm = messagebox.askyesno(
            "Confirmer l'enregistrement",
            f"Remplacer le fichier distant ?\n\n"
            f"{self.loaded_path}\n\n"
            "Une copie de sauvegarde sera créée avant le remplacement."
        )

        if not confirm:
            return

        self._worker(
            self._save_worker,
            self.loaded_path,
            content,
            self.file_encoding,
            self.file_has_bom,
            self.loaded_mode,
            self.loaded_mtime
        )

    def _save_worker(
        self, path, content, encoding, bom,
        mode, expected_mtime
    ):

        current_stat = self.sftp.stat(path)

        if (
            expected_mtime is not None
            and current_stat.st_mtime != expected_mtime
        ):
            raise RuntimeError(
                "Le fichier distant a changé depuis son chargement. "
                "Rechargez-le avant d'enregistrer."
            )

        raw = content.encode(encoding)

        if bom and encoding == "utf-8":
            raw = b"\xef\xbb\xbf" + raw

        parent = str(PurePosixPath(path).parent)
        name = PurePosixPath(path).name

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]

        backup = f"{path}.bak-{stamp}-{unique}"
        temp = f"{parent}/.{name}.tmp-{unique}"
        displaced = f"{path}.old-{unique}"

        # 1. Sauvegarde du fichier original.
        # Mode "wb" évite le mode exclusif "xb" qui a posé problème.
        with self.sftp.open(path, "rb") as source:
            with self.sftp.open(backup, "wb") as destination:

                while True:
                    chunk = source.read(1024 * 1024)

                    if not chunk:
                        break

                    destination.write(chunk)

                destination.flush()

        # 2. Écrit d'abord le nouveau contenu dans un fichier temporaire.
        try:

            with self.sftp.open(temp, "wb") as remote_file:
                remote_file.write(raw)
                remote_file.flush()

            if mode is not None:
                self.sftp.chmod(temp, mode & 0o7777)

            # 3. Remplacement de l'original après écriture complète.
            self.sftp.rename(path, displaced)

            try:
                self.sftp.rename(temp, path)

            except Exception:
                # Tente de restaurer le fichier original.
                self.sftp.rename(displaced, path)
                raise

            # La copie .bak-* reste disponible.
            try:
                self.sftp.remove(displaced)
            except Exception:
                pass

        except Exception:

            try:
                self.sftp.remove(temp)
            except Exception:
                pass

            raise

        new_stat = self.sftp.stat(path)

        return content, new_stat.st_mtime

    # ==================================================
    # FICHIERS LOCAUX
    # ==================================================

    def open_local_file(self):

        path = filedialog.askopenfilename(
            title="Ouvrir un fichier texte"
        )

        if not path:
            return

        if self.dirty:
            if not messagebox.askyesno(
                "Modifications non enregistrées",
                "Abandonner les modifications actuelles ?"
            ):
                return

        self._worker(self._local_load_worker, path)

    def _local_load_worker(self, path):

        size = os.path.getsize(path)

        if size > MAX_FILE_BYTES:
            raise ValueError(
                f"Fichier trop volumineux : {size} octets."
            )

        with open(path, "rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)

        if b"\x00" in raw:
            raise ValueError("Le fichier semble binaire.")

        bom = raw.startswith(b"\xef\xbb\xbf")

        try:
            content = raw.decode(
                "utf-8-sig" if bom else "utf-8"
            )
            encoding = "utf-8"

        except UnicodeDecodeError:
            content = raw.decode("latin-1")
            encoding = "latin-1"

        return content, encoding, bom, path

    def save_local_copy(self):

        path = filedialog.asksaveasfilename(
            title="Enregistrer une copie locale",
            defaultextension=".txt",
            filetypes=[
                (
                    "Fichiers texte",
                    "*.txt *.conf *.ini *.php *.py *.sh *.json *.yaml *.yml"
                ),
                ("Tous les fichiers", "*.*")
            ]
        )

        if not path:
            return

        content = self.editor.get("1.0", "end-1c")

        raw = content.encode(self.file_encoding)

        if self.file_has_bom and self.file_encoding == "utf-8":
            raw = b"\xef\xbb\xbf" + raw

        try:

            with open(path, "wb") as file:
                file.write(raw)

            self.status_var.set(
                f"Copie locale enregistrée : {path}"
            )

        except OSError as exc:
            messagebox.showerror("Erreur", str(exc))

    # ==================================================
    # FERMETURE
    # ==================================================

    def on_close(self):

        if self.busy:
            messagebox.showwarning(
                "Opération en cours",
                "Attendez la fin de l'opération avant de quitter."
            )
            return

        if self.dirty:
            if not messagebox.askyesno(
                "Quitter",
                "Des modifications ne sont pas enregistrées. "
                "Voulez-vous vraiment quitter ?"
            ):
                return

        try:
            if self.sftp:
                self.sftp.close()

            if self.client:
                self.client.close()

        except Exception:
            pass

        self.destroy()


if __name__ == "__main__":
    app = SSHFileEditor()
    app.mainloop()
