#!/usr/bin/env python3
"""
A GUI application for automatically tagging audio files using MusicBrainz,
TheAudioDB, Deezer, and Lyrics.ovh APIs.

Author: Gino Bogo

Features: - Select audio files (MP3 or Opus) - Parse artist and title from
filename - Fetch metadata from MusicBrainz and public APIs - Display current
file tags - Choose from multiple metadata options - Apply selected metadata as
tags - Handle cover art from multiple sources
"""

import base64
import os
import sys
import configparser
import requests
from typing import Optional

import musicbrainzngs
from mutagen._util import MutagenError
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.id3._frames import TALB, TDRC, TPE1, TIT2, TCON, APIC, TRCK
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QCloseEvent, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# =============================================================================
# CONSTANTS
# =============================================================================

RIGHT_VCENTER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
NO_FILE_SELECTED_TEXT = "No file selected."
CONFIG_FILE_NAME = "auto_song_tagger.cfg"

# MusicBrainz client setup
musicbrainzngs.set_useragent("AutoSongTagger", "0.1", "your-email@example.com")

# =============================================================================
# PUBLIC MUSIC APIs
# =============================================================================


class PublicMusicAPIs:
    """Manages public music APIs that don't require authentication."""

    def search_audiodb(self, artist: str, title: str) -> list[dict]:
        """Search TheAudioDB API (free, no authentication required)."""
        try:
            url = "https://theaudiodb.com/api/v1/json/2/searchtrack.php"
            params = {"s": artist, "t": title}
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = []
                tracks = data.get("track", [])
                for track in tracks:
                    # Extract year from release date
                    year = ""
                    release_date = track.get("intYearReleased", "")
                    if release_date and len(str(release_date)) >= 4:
                        year = str(release_date)[:4]

                    results.append(
                        {
                            "title": track.get("strTrack", ""),
                            "artist": track.get("strArtist", ""),
                            "album": track.get("strAlbum", ""),
                            "year": year,
                            "track": track.get("strTrackNumber", ""),
                            "genre": track.get("strGenre", ""),
                            "cover_url": track.get("strTrackThumb", "")
                            or track.get("strAlbumThumb", ""),
                            "source": "TheAudioDB",
                        }
                    )
                return results
        except Exception as e:
            print(f"TheAudioDB search error: {e}")
        return []

    def search_lrcat(self, artist: str, title: str) -> list[dict]:
        """Search Lyrics.ovh API for basic track info."""
        try:
            url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return [
                    {
                        "title": title,
                        "artist": artist,
                        "album": "",
                        "year": "",
                        "track": "",
                        "genre": "",
                        "source": "Lyrics.ovh",
                        "has_lyrics": True,
                    }
                ]
        except Exception as e:
            print(f"Lyrics.ovh search error: {e}")
        return []

    def search_musicbrainz_cover_art(self, release_id: str) -> Optional[str]:
        """Get cover art from MusicBrainz Cover Art Archive."""
        try:
            url = f"https://coverartarchive.org/release/{release_id}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                images = data.get("images", [])
                if images:
                    front_covers = [img for img in images if img.get("front", False)]
                    if front_covers:
                        return front_covers[0].get("image")
                    else:
                        return images[0].get("image")
        except Exception as e:
            print(f"Cover Art Archive error: {e}")
        return None

    def search_deezer(self, artist: str, title: str) -> list[dict]:
        """Search Deezer API (limited free access without credentials)."""
        try:
            url = "https://api.deezer.com/search"
            params = {"q": f'artist:"{artist}" track:"{title}"', "limit": 5}
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                results = []
                for track in data.get("data", []):
                    album = track.get("album", {})

                    year = ""
                    release_date = track.get("release_date", "")
                    if release_date and len(release_date) >= 4:
                        year = release_date[:4]

                    results.append(
                        {
                            "title": track.get("title", ""),
                            "artist": track.get("artist", {}).get("name", ""),
                            "album": album.get("title", ""),
                            "year": year,
                            "track": str(track.get("track_position", "")),
                            "genre": "",
                            "cover_url": album.get("cover_medium", "")
                            or album.get("cover", ""),
                            "source": "Deezer",
                        }
                    )
                return results
        except Exception as e:
            print(f"Deezer search error: {e}")
        return []

    def download_cover_art(self, url: str) -> Optional[bytes]:
        """Download cover art from URL."""
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                return response.content
        except Exception as e:
            print(f"Error downloading cover art: {e}")
        return None


# =============================================================================
# THREADING CLASSES
# =============================================================================


class TagWriterThread(QThread):
    """QThread for performing tag writing in a separate thread."""

    finished = Signal(bool, str)
    progress_signal = Signal(str)

    def __init__(self, song_file: str, metadata: dict, cover_data: bytes | None):
        super().__init__()
        self.song_file = song_file
        self.metadata = metadata
        self.cover_data = cover_data

    def run(self):
        """Main thread execution method."""
        try:
            self.progress_signal.emit("Starting tag update...")
            write_tags(self.song_file, self.metadata, self.cover_data)
            self.finished.emit(True, "ID3 tags updated successfully!")
        except Exception as e:
            self.finished.emit(False, f"Failed to apply tags: {e}")


class MetadataFetcherThread(QThread):
    """QThread for fetching metadata in a separate thread."""

    finished = Signal(list)
    progress_signal = Signal(str)

    def __init__(self, artist: str, title: str):
        super().__init__()
        self.artist = artist
        self.title = title
        self.public_apis = PublicMusicAPIs()

    def run(self):
        """Main thread execution method."""
        metadata_options = []

        # MusicBrainz search
        self.progress_signal.emit("Searching MusicBrainz...")
        musicbrainz_results = fetch_song_metadata(self.artist, self.title)

        # Enhance with cover art
        for result in musicbrainz_results:
            if "release_id" in result:
                cover_url = self.public_apis.search_musicbrainz_cover_art(
                    result["release_id"]
                )
                if cover_url:
                    result["cover_url"] = cover_url
                    result["source"] = "MusicBrainz (with cover)"

        metadata_options.extend(musicbrainz_results)

        # Public APIs
        self.progress_signal.emit("Searching TheAudioDB...")
        metadata_options.extend(
            self.public_apis.search_audiodb(self.artist, self.title)
        )

        self.progress_signal.emit("Searching Deezer...")
        metadata_options.extend(self.public_apis.search_deezer(self.artist, self.title))

        # Lyrics.ovh as fallback
        if not metadata_options:
            self.progress_signal.emit("Searching Lyrics.ovh...")
            metadata_options.extend(
                self.public_apis.search_lrcat(self.artist, self.title)
            )

        self.finished.emit(metadata_options)


# =============================================================================
# CUSTOM WIDGETS
# =============================================================================


class ClickableLabel(QLabel):
    """QLabel that emits a clicked signal when pressed."""

    clicked = Signal()

    def mousePressEvent(self, event: QMouseEvent):
        self.clicked.emit()
        super().mousePressEvent(event)


# =============================================================================
# MUSICBRAINZ API FUNCTIONS
# =============================================================================


def _get_track_number(release: dict, recording_id: str) -> str:
    """Find the track number for a recording within a release."""
    if "medium-list" in release:
        for medium in release["medium-list"]:
            if "track-list" in medium:
                for track in medium["track-list"]:
                    if track.get("recording", {}).get("id") == recording_id:
                        return track.get("number", "")
    return ""


def _get_genre(recording: dict) -> str:
    """Extract genre from a recording's tag list."""
    if "tag-list" in recording and recording["tag-list"]:
        return ", ".join([tag["name"] for tag in recording["tag-list"]])
    return ""


def _choose_release(release_list: list) -> dict:
    """Choose the best release from a list of releases."""
    releases_with_date = [r for r in release_list if "date" in r]
    return releases_with_date[0] if releases_with_date else release_list[0]


def _fetch_and_cache_release_details(
    release_id: str, release_cache: dict
) -> dict | None:
    """Fetch release details from MusicBrainz and cache them."""
    if release_id in release_cache:
        return release_cache[release_id]

    try:
        release_details = musicbrainzngs.get_release_by_id(
            release_id, includes=["recordings"]
        )
        release_cache[release_id] = release_details
        return release_details
    except musicbrainzngs.WebServiceError as exc:
        print(f"Error fetching release details from MusicBrainz: {exc}")
        return None


def _process_recording(recording: dict, artist: str, release_cache: dict) -> dict:
    """Process a single recording from MusicBrainz search result."""
    album, year, track = "", "", ""

    # Get release info
    if "release-list" in recording and recording["release-list"]:
        chosen_release = _choose_release(recording["release-list"])
        album = chosen_release.get("title", "")

        date_str = chosen_release.get("date", "")
        if len(date_str) >= 4 and date_str[:4].isdigit():
            year = date_str[:4]

        release_id = chosen_release.get("id")
        if release_id:
            release_details = _fetch_and_cache_release_details(
                release_id, release_cache
            )
            if release_details and "release" in release_details:
                track = _get_track_number(release_details["release"], recording["id"])

    # Get genre
    genre = _get_genre(recording)

    # Store release_id for cover art
    release_id = ""
    if "release-list" in recording and recording["release-list"]:
        chosen_release = _choose_release(recording["release-list"])
        release_id = chosen_release.get("id", "")

    return {
        "title": recording.get("title", ""),
        "artist": artist,
        "album": album,
        "year": year,
        "track": track,
        "genre": genre,
        "source": "MusicBrainz",
        "release_id": release_id,
        "cover_url": None,
    }


def fetch_song_metadata(artist: str, title: str) -> list[dict]:
    """Fetch song metadata from MusicBrainz based on artist and title."""
    try:
        result = musicbrainzngs.search_recordings(artist=artist, recording=title)
    except musicbrainzngs.WebServiceError as exc:
        print(f"Error fetching metadata from MusicBrainz: {exc}")
        return []

    if not result.get("recording-list"):
        return []

    release_cache = {}
    return [
        _process_recording(rec, artist, release_cache)
        for rec in result["recording-list"]
    ]


# =============================================================================
# AUDIO FILE HANDLING FUNCTIONS
# =============================================================================


def get_audio_file(file_path: str) -> MP3 | OggOpus:
    """Factory function to return the correct mutagen audio object."""
    _, ext = os.path.splitext(file_path)

    if ext.lower() == ".mp3":
        return MP3(file_path)
    elif ext.lower() == ".opus":
        return OggOpus(file_path)
    else:
        raise MutagenError(f"Unsupported file type: {ext}")


def write_tags(song_file: str, metadata: dict, cover_data: bytes | None = None):
    """Write tags and optionally cover art to an audio file."""
    try:
        audio = get_audio_file(song_file)
    except MutagenError as e:
        print(f"Error loading {song_file}: {e}")
        raise

    # Write basic tags
    if isinstance(audio, MP3):
        _write_mp3_tags(audio, metadata)
        if cover_data:
            _write_mp3_cover(audio, cover_data)
    elif isinstance(audio, OggOpus):
        _write_ogg_opus_tags(audio, metadata)
        if cover_data:
            _write_ogg_opus_cover(audio, cover_data)

    audio.save()


def _write_mp3_tags(audio: MP3, metadata: dict):
    """Write MP3 specific tags."""
    if audio.tags is None:
        audio.tags = ID3()

    if metadata.get("artist"):
        audio.tags["TPE1"] = TPE1(encoding=3, text=[metadata["artist"]])

    if metadata.get("title"):
        audio.tags["TIT2"] = TIT2(encoding=3, text=[metadata["title"]])

    if metadata.get("album"):
        audio.tags["TALB"] = TALB(encoding=3, text=[metadata["album"]])

    if metadata.get("year"):
        audio.tags["TDRC"] = TDRC(encoding=3, text=[metadata["year"][:4]])

    if metadata.get("track"):
        audio.tags["TRCK"] = TRCK(encoding=3, text=[metadata["track"]])

    if metadata.get("genre"):
        audio.tags["TCON"] = TCON(encoding=3, text=[metadata["genre"]])


def _write_ogg_opus_tags(audio: OggOpus, metadata: dict):
    """Write OggOpus specific tags."""
    if audio.tags is None:
        audio.add_tags()

    if metadata.get("artist"):
        audio.tags["artist"] = metadata["artist"]

    if metadata.get("title"):
        audio.tags["title"] = metadata["title"]

    if metadata.get("album"):
        audio.tags["album"] = metadata["album"]

    if metadata.get("year"):
        audio.tags["date"] = metadata["year"]

    if metadata.get("track"):
        audio.tags["tracknumber"] = metadata["track"]

    if metadata.get("genre"):
        audio.tags["genre"] = metadata["genre"]


def _write_mp3_cover(audio: MP3, cover_data: bytes):
    """Write MP3 cover art."""
    if audio.tags is None:
        audio.tags = ID3()

    audio.tags.delall("APIC")
    audio.tags.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Cover",
            data=cover_data,
        )
    )


def _write_ogg_opus_cover(audio: OggOpus, cover_data: bytes):
    """Write OggOpus cover art."""
    if audio.tags is None:
        audio.add_tags()

    picture = Picture()
    picture.data = cover_data
    picture.type = 3
    picture.mime = "image/jpeg"

    audio.tags["metadata_block_picture"] = [
        base64.b64encode(picture.write()).decode("ascii")
    ]


def parse_artist_title_from_filename(filename: str) -> tuple[str | None, str | None]:
    """Parse artist and title from a filename (Artist - Title.ext)."""
    base_name = os.path.splitext(os.path.basename(filename))[0]

    if " - " in base_name:
        parts = base_name.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
        return artist, title

    return None, None


# =============================================================================
# MAIN APPLICATION CLASS
# =============================================================================


class AutoSongTaggerUI(QWidget):
    """Main application window for Auto Song Tagger."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Auto Song Tagger")

        # Initialize instance variables
        self._new_cover_data = None
        self._original_tags = {}
        self._column_widths_from_settings = []
        self.song_file_path = ""
        self.metadata_options = []

        # Declare UI elements for static analysis
        self.current_artist_input: Optional[QLineEdit] = None
        self.current_title_input: Optional[QLineEdit] = None
        self.current_album_input: Optional[QLineEdit] = None
        self.current_year_input: Optional[QLineEdit] = None
        self.current_track_input: Optional[QLineEdit] = None
        self.current_genre_input: Optional[QLineEdit] = None

        # Setup UI
        self.load_settings()
        self.init_ui()
        self.apply_column_widths_from_settings()
        self._apply_styles()

    # =========================================================================
    # SETTINGS MANAGEMENT
    # =========================================================================

    def load_settings(self):
        """Load window size, position, and column widths from config file."""
        config = configparser.ConfigParser()
        config_file = CONFIG_FILE_NAME

        if os.path.exists(config_file):
            config.read(config_file)

            # Load window geometry
            if "MainWindow" in config:
                try:
                    x = int(config["MainWindow"]["x"])
                    y = int(config["MainWindow"]["y"])
                    width = int(config["MainWindow"]["width"])
                    height = int(config["MainWindow"]["height"])
                    self.setGeometry(x, y, width, height)
                except ValueError:
                    print("Error reading window geometry from config. Using defaults.")

            # Load column widths
            if "ColumnWidths" in config:
                try:
                    widths_str = config["ColumnWidths"]["widths"]
                    widths = [int(w) for w in widths_str.split(",")]
                    self._column_widths_from_settings = widths
                except ValueError:
                    print("Error reading column widths from config. Using defaults.")

    def apply_column_widths_from_settings(self):
        """Apply column widths from settings to the results table."""
        if getattr(self, "_column_widths_from_settings", []):
            header = self.results_list.horizontalHeader()
            for i, width in enumerate(self._column_widths_from_settings):
                if i < header.count():
                    header.resizeSection(i, width)

    def save_settings(self):
        """Save current window size, position, and column widths to config file."""
        config = configparser.ConfigParser()

        config["MainWindow"] = {
            "x": str(self.x()),
            "y": str(self.y()),
            "width": str(self.width()),
            "height": str(self.height()),
        }

        # Save column widths
        header = self.results_list.horizontalHeader()
        column_widths = [str(header.sectionSize(i)) for i in range(header.count())]
        config["ColumnWidths"] = {"widths": ",".join(column_widths)}

        with open(CONFIG_FILE_NAME, "w") as config_file:
            config.write(config_file)

    def closeEvent(self, event: QCloseEvent):
        """Override close event to save window settings."""
        self.save_settings()
        event.accept()

    # =========================================================================
    # UI INITIALIZATION AND STYLING
    # =========================================================================

    def init_ui(self):
        """Initialize the user interface components and layout."""
        main_layout = QVBoxLayout()

        # File Selection Section
        main_layout.addLayout(self._create_file_selection_section())

        # Artist/Title Input Section
        main_layout.addLayout(self._create_input_section())

        # Action Buttons Section
        main_layout.addLayout(self._create_button_section())

        # Metadata Results Section
        main_layout.addLayout(self._create_results_section())

        # Current Tags Section
        main_layout.addLayout(self._create_current_tags_section())

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        self.setLayout(main_layout)

    def _create_file_selection_section(self) -> QHBoxLayout:
        """Create the file selection section."""
        layout = QHBoxLayout()

        self.file_label = QLabel("Audio File:")
        self.file_label.setFixedWidth(80)
        self.file_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)

        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)

        self.browse_button = QPushButton("Browse")
        self.browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_button.setFixedWidth(100)
        self.browse_button.clicked.connect(self.browse_song_file)

        layout.addWidget(self.file_label)
        layout.addWidget(self.file_path_input)
        layout.addWidget(self.browse_button)

        return layout

    def _create_input_section(self) -> QHBoxLayout:
        """Create the artist/title input section."""
        layout = QHBoxLayout()

        self.artist_label = QLabel("Artist:")
        self.artist_label.setFixedWidth(80)
        self.artist_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.artist_input = QLineEdit()

        self.title_label = QLabel("Title:")
        self.title_label.setFixedWidth(80)
        self.title_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.title_input = QLineEdit()

        layout.addWidget(self.artist_label)
        layout.addWidget(self.artist_input)
        layout.addWidget(self.title_label)
        layout.addWidget(self.title_input)

        return layout

    def _create_button_section(self) -> QHBoxLayout:
        """Create the action buttons section."""
        layout = QHBoxLayout()

        self.fetch_button = QPushButton("Fetch Metadata")
        self.fetch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_button.clicked.connect(self.fetch_metadata)

        self.apply_button = QPushButton("Apply Tags")
        self.apply_button.setObjectName("applyButton")
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.clicked.connect(self.apply_tags)
        self.apply_button.setEnabled(False)

        layout.addWidget(self.fetch_button)
        layout.addWidget(self.apply_button)

        return layout

    def _create_results_section(self) -> QVBoxLayout:
        """Create the metadata results section."""
        layout = QVBoxLayout()

        self.results_label = QLabel("Metadata Options:")
        self.results_list = QTableWidget()
        self.results_list.setColumnCount(7)
        self.results_list.setHorizontalHeaderLabels(
            ["Source", "Artist", "Title", "Album", "Year", "Track", "Genre"]
        )

        header = self.results_list.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.results_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.results_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.results_list.itemSelectionChanged.connect(self.enable_apply_button)

        layout.addWidget(self.results_label)
        layout.addWidget(self.results_list)

        return layout

    def _create_current_tags_section(self) -> QVBoxLayout:
        """Create the current tags display section."""
        layout = QVBoxLayout()

        self.current_tags_label = QLabel("Current Tags:")
        tags_and_cover_layout = QHBoxLayout()

        # Tags input fields
        tags_layout = self._create_tags_input_layout()

        # Disc cover placeholder
        self.disc_cover_label = ClickableLabel("Disc Cover")
        self.disc_cover_label.setFixedSize(256, 256)
        self.disc_cover_label.setStyleSheet(
            "background-color: #e0e0e0; border: 1px solid #ccc;"
        )
        self.disc_cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.disc_cover_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disc_cover_label.clicked.connect(self._on_disc_cover_clicked)

        tags_and_cover_layout.addLayout(tags_layout)
        tags_and_cover_layout.addWidget(self.disc_cover_label)

        layout.addWidget(self.current_tags_label)
        layout.addLayout(tags_and_cover_layout)

        return layout

    def _create_tags_input_layout(self) -> QVBoxLayout:
        """Create the layout for tag input fields."""
        layout = QVBoxLayout()

        fields = [
            ("Artist", "current_artist_input"),
            ("Title", "current_title_input"),
            ("Album", "current_album_input"),
            ("Year", "current_year_input"),
            ("Track", "current_track_input"),
            ("Genre", "current_genre_input"),
        ]

        for label_text, input_name in fields:
            field_layout = QHBoxLayout()

            label = QLabel(f"{label_text}:")
            label.setFixedWidth(60)
            label.setAlignment(RIGHT_VCENTER_ALIGNMENT)

            input_field = QLineEdit()
            input_field.textChanged.connect(self._on_current_tag_text_changed)

            setattr(self, input_name, input_field)

            field_layout.addWidget(label)
            field_layout.addWidget(input_field)
            layout.addLayout(field_layout)

        return layout

    def _apply_styles(self):
        """Apply CSS styles to the application."""
        self.setStyleSheet(
            """
            QWidget {
                background-color: #f0f0f0;
                color: #333;
            }
            QLineEdit, QTextEdit, QTableWidget {
                background-color: #fff;
                border: 1px solid #ccc;
                border-radius: 4px;
                padding: 5px;
            }
            QPushButton {
                background-color: #0078d7;
                color: #fff;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #005a9e;
            }
            QPushButton#applyButton {
                background-color: #FFFF00;
                color: #000000;
            }
            QPushButton#applyButton:hover {
                background-color: #CCCC00;
            }
            QPushButton#applyButton:pressed {
                background-color: #999900;
            }
            QPushButton#applyButton:disabled {
                background-color: #E0E0A0;
                color: #606060;
            }
            QPushButton:pressed {
                background-color: #004578;
            }
            QPushButton:disabled {
                background-color: #d3d3d3;
                color: #888;
            }
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
            }
        """
        )

    # =========================================================================
    # FILE OPERATIONS
    # =========================================================================

    def browse_song_file(self):
        """Open file dialog to select an audio file and update UI."""
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilter("Audio files (*.mp3 *.opus)")

        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                self.song_file_path = selected_files[0]
                self.file_path_input.setText(self.song_file_path)
                self.display_current_tags()
                self.display_current_cover()
                self.parse_filename_for_artist_title()
                self.results_list.clear()
                self.results_list.setHorizontalHeaderLabels(
                    ["Source", "Artist", "Title", "Album", "Year", "Track", "Genre"]
                )
                self.apply_button.setEnabled(False)

    def parse_filename_for_artist_title(self):
        """Parse artist and title from selected filename."""
        if self.song_file_path:
            artist, title = parse_artist_title_from_filename(self.song_file_path)
            if artist and title:
                self.artist_input.setText(artist)
                self.title_input.setText(title)

    # =========================================================================
    # TAG DISPLAY AND EXTRACTION
    # =========================================================================

    def display_current_tags(self):
        """Display current tags of the selected audio file."""
        if not self.song_file_path:
            self._clear_tag_fields(NO_FILE_SELECTED_TEXT)
            return

        try:
            audio = get_audio_file(self.song_file_path)
        except MutagenError:
            self._clear_tag_fields(f"Error loading {self.song_file_path}")
            return

        if not audio.tags:
            self._clear_tag_fields("No tags found.")
            return

        # Extract tags based on file type
        if isinstance(audio, MP3):
            tags = self._extract_mp3_tags(audio)
        elif isinstance(audio, OggOpus):
            tags = self._extract_ogg_tags(audio)
        else:
            return

        self._populate_tag_fields(tags)
        self._original_tags = tags
        self._on_current_tag_text_changed()

    def _clear_tag_fields(self, message: str | None = None):
        """Clear the tag display fields."""
        # Check if input fields are initialized before attempting to set text or clear
        # This prevents errors if _clear_tag_fields is called before init_ui completes
        if (
            hasattr(self, "current_artist_input")
            and self.current_artist_input is not None
        ):
            self.current_artist_input.setText(message if message is not None else "")
        if (
            hasattr(self, "current_title_input")
            and self.current_title_input is not None
        ):
            self.current_title_input.clear()
        if (
            hasattr(self, "current_album_input")
            and self.current_album_input is not None
        ):
            self.current_album_input.clear()
        if hasattr(self, "current_year_input") and self.current_year_input is not None:
            self.current_year_input.clear()
        if (
            hasattr(self, "current_track_input")
            and self.current_track_input is not None
        ):
            self.current_track_input.clear()
        if (
            hasattr(self, "current_genre_input")
            and self.current_genre_input is not None
        ):
            self.current_genre_input.clear()

    def _extract_mp3_tags(self, audio: MP3) -> dict[str, str]:
        """Extract ID3 tags from an MP3 file."""
        tags = audio.tags
        year = "N/A"

        if tags and "TDRC" in tags and tags["TDRC"].text:
            year_str = str(tags["TDRC"].text[0])
            if len(year_str) >= 4 and year_str[:4].isdigit():
                year = year_str[:4]

        def get_tag(tag_name, default="N/A"):
            return (
                str(tags[tag_name].text[0])
                if tags and tag_name in tags and tags[tag_name].text
                else default
            )

        return {
            "artist": get_tag("TPE1"),
            "title": get_tag("TIT2"),
            "album": get_tag("TALB"),
            "year": year,
            "track": get_tag("TRCK"),
            "genre": get_tag("TCON"),
        }

    def _extract_ogg_tags(self, audio: OggOpus) -> dict[str, str]:
        """Extract tags from an OggOpus file."""
        tags = audio.tags
        if tags is None:
            return dict.fromkeys(
                ["artist", "title", "album", "year", "track", "genre"], "N/A"
            )

        def get_tag(tag_name, default="N/A"):
            return tags[tag_name][0] if tag_name in tags else default

        return {
            "artist": get_tag("artist"),
            "title": get_tag("title"),
            "album": get_tag("album"),
            "year": get_tag("date"),
            "track": (
                get_tag("tracknumber").split("/")[0]
                if isinstance(get_tag("tracknumber"), str)
                and "/" in get_tag("tracknumber")
                else get_tag("tracknumber")
            ),
            "genre": get_tag("genre"),
        }

    def _populate_tag_fields(self, tags: dict[str, str]):
        """Populate UI fields with the given tags."""
        # Ensure input fields are initialized before setting text
        if self.current_artist_input:
            self.current_artist_input.setText(tags.get("artist", "N/A"))
        if self.current_title_input:
            self.current_title_input.setText(tags.get("title", "N/A"))
        if self.current_album_input:
            self.current_album_input.setText(tags.get("album", "N/A"))
        if self.current_year_input:
            self.current_year_input.setText(tags.get("year", "N/A"))
        if self.current_track_input:
            self.current_track_input.setText(tags.get("track", "N/A"))
        if self.current_genre_input:
            self.current_genre_input.setText(tags.get("genre", "N/A"))

    def _get_input_text_value(self, input_widget: Optional[QLineEdit]) -> str:
        """Safely get text from a QLineEdit widget, returning an empty string if None."""
        return input_widget.text() if input_widget else ""

    # =========================================================================
    # COVER ART HANDLING
    # =========================================================================

    def display_current_cover(self):
        """Display current album cover from audio file."""
        if not self.song_file_path:
            self.disc_cover_label.clear()
            self.disc_cover_label.setText(NO_FILE_SELECTED_TEXT)
            return

        try:
            audio = get_audio_file(self.song_file_path)
        except MutagenError:
            self.disc_cover_label.clear()
            self.disc_cover_label.setText("Error loading file.")
            return

        cover_data = None
        if isinstance(audio, MP3):
            cover_data = self._extract_mp3_cover(audio)
        elif isinstance(audio, OggOpus):
            cover_data = self._extract_ogg_opus_cover(audio)

        self._display_cover_image(cover_data)

    def _extract_mp3_cover(self, audio: MP3) -> bytes | None:
        """Extract cover data from MP3 file."""
        if audio.tags:
            apic_frames = audio.tags.getall("APIC")
            if apic_frames:
                return apic_frames[0].data
        return None

    def _extract_ogg_opus_cover(self, audio: OggOpus) -> bytes | None:
        """Extract cover data from OggOpus file."""
        if audio.tags is not None and "metadata_block_picture" in audio.tags:
            try:
                cover_data = base64.b64decode(audio.tags["metadata_block_picture"][0])
                picture = Picture(cover_data)
                return picture.data
            except Exception:
                pass
        return None

    def _display_cover_image(self, cover_data: bytes | None):
        """Display cover image in the label."""
        if cover_data:
            pixmap = QPixmap()
            pixmap.loadFromData(cover_data)
            scaled_pixmap = pixmap.scaled(
                self.disc_cover_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.disc_cover_label.setPixmap(scaled_pixmap)
            self.disc_cover_label.setText("")
        else:
            self.disc_cover_label.clear()
            self.disc_cover_label.setText("No cover found.")

    def _on_disc_cover_clicked(self):
        """Handle click event on disc cover to select new cover image."""
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilter("Image files (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")

        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()
            if selected_files:
                image_path = selected_files[0]
                try:
                    with open(image_path, "rb") as f:
                        self._new_cover_data = f.read()
                    self._display_cover_image(self._new_cover_data)
                except Exception as e:
                    QMessageBox.warning(
                        self, "Error Loading Image", f"Could not load image: {e}"
                    )
                    self._new_cover_data = None

    # =========================================================================
    # METADATA FETCHING AND APPLICATION
    # =========================================================================

    def fetch_metadata(self):
        """Fetch metadata from APIs and populate results list."""
        artist = self.artist_input.text().strip()
        title = self.title_input.text().strip()

        if not self.song_file_path:
            QMessageBox.warning(
                self, "Input Error", "Please select an audio file first."
            )
            return

        if not artist or not title:
            QMessageBox.warning(
                self, "Input Error", "Please provide both Artist and Title."
            )
            return

        self.results_list.setRowCount(0)
        self.apply_button.setEnabled(False)

        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Fetching metadata...")
        self.progress_bar.show()

        self.metadata_fetcher_thread = MetadataFetcherThread(artist, title)
        self.metadata_fetcher_thread.finished.connect(self._on_metadata_fetched)
        self.metadata_fetcher_thread.progress_signal.connect(self._on_progress_update)
        self.metadata_fetcher_thread.start()

    def _on_metadata_fetched(self, metadata_options: list[dict]):
        """Handle the result of metadata fetching thread."""
        self.progress_bar.hide()

        if not metadata_options:
            QMessageBox.information(
                self, "No Results", "No metadata found for this artist and title."
            )
            return

        self.metadata_options = metadata_options

        # Sort by year ascending
        self.metadata_options.sort(
            key=lambda x: (
                int(x.get("year", "9999")[:4])
                if x.get("year", "").strip().isdigit()
                else 9999
            )
        )

        # Populate results table
        for meta in self.metadata_options:
            row_position = self.results_list.rowCount()
            self.results_list.insertRow(row_position)

            fields = ["source", "artist", "title", "album", "year", "track", "genre"]
            for col, field in enumerate(fields):
                item = QTableWidgetItem(meta.get(field, ""))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.results_list.setItem(row_position, col, item)

        self.results_list.resizeColumnsToContents()

    def enable_apply_button(self):
        """Enable apply button when a row is selected."""
        selected_indexes = self.results_list.selectedIndexes()

        if selected_indexes:
            self.apply_button.setEnabled(True)
            selected_row = selected_indexes[0].row()

            # Populate current tag fields with selected metadata
            fields = ["source", "artist", "title", "album", "year", "track", "genre"]
            field_data = {}

            for col, field in enumerate(fields):
                item = self.results_list.item(selected_row, col)
                field_data[field] = item.text() if item else ""

            for field in ["artist", "title", "album", "year", "track", "genre"]:
                getattr(self, f"current_{field}_input").setText(
                    field_data.get(field, "")
                )

            # Download and display cover art if available
            if selected_row < len(self.metadata_options):
                metadata = self.metadata_options[selected_row]
                cover_url = metadata.get("cover_url")
                if cover_url:
                    self._download_and_display_cover(cover_url)

            self._on_current_tag_text_changed()

    def _download_and_display_cover(self, cover_url: str):
        """Download and display cover art from URL."""
        if cover_url:
            self.disc_cover_label.setText("Downloading cover...")

            apis = PublicMusicAPIs()
            cover_data = apis.download_cover_art(cover_url)
            if cover_data:
                self._new_cover_data = cover_data
                self._display_cover_image(cover_data)
            else:
                self.disc_cover_label.setText("Failed to download cover")

    def apply_tags(self):
        """Apply selected metadata as tags to audio file."""
        chosen_metadata = {
            "artist": self._get_input_text_value(self.current_artist_input),
            "title": self._get_input_text_value(self.current_title_input),
            "album": self._get_input_text_value(self.current_album_input),
            "year": self._get_input_text_value(self.current_year_input),
            "track": self._get_input_text_value(self.current_track_input),
            "genre": self._get_input_text_value(self.current_genre_input),
        }

        if not any(chosen_metadata.values()):
            QMessageBox.warning(self, "Error", "No metadata to apply.")
            return

        self.apply_button.setEnabled(False)

        self.tag_writer_thread = TagWriterThread(
            self.song_file_path, chosen_metadata, self._new_cover_data
        )
        self.tag_writer_thread.finished.connect(self._on_tags_written)
        self.tag_writer_thread.progress_signal.connect(self._on_progress_update)
        self.tag_writer_thread.start()

    def _on_tags_written(self, success: bool, message: str):
        """Handle the result of tag writing thread."""
        if success:
            QMessageBox.information(self, "Tags Applied", message)
            self.display_current_tags()
            self.display_current_cover()
            self._new_cover_data = None
        else:
            QMessageBox.warning(self, "Error", message)

        self.apply_button.setEnabled(True)
        self.progress_bar.hide()

    def _on_progress_update(self, message: str):
        """Update progress bar with messages."""
        self.progress_bar.show()
        self.progress_bar.setFormat(message)
        self.progress_bar.setRange(0, 0)

    def _on_current_tag_text_changed(self):
        """Enable apply button if changes detected in current tag fields."""
        if not self.song_file_path:
            self.apply_button.setEnabled(False)
            return

        current_tags = {
            "artist": self._get_input_text_value(self.current_artist_input),
            "title": self._get_input_text_value(self.current_title_input),
            "album": self._get_input_text_value(self.current_album_input),
            "year": self._get_input_text_value(self.current_year_input),
            "track": self._get_input_text_value(self.current_track_input),
            "genre": self._get_input_text_value(self.current_genre_input),
        }

        # Check if changes were made from original tags
        tags_changed = (
            hasattr(self, "_original_tags") and self._original_tags != current_tags
        )

        # Enable if tags changed OR a row is selected
        row_selected = bool(self.results_list.selectedIndexes())
        self.apply_button.setEnabled(tags_changed or row_selected)


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AutoSongTaggerUI()
    window.show()
    sys.exit(app.exec())
