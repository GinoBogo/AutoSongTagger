#!/usr/bin/env python3

"""
A GUI application for automatically tagging audio files using MusicBrainz
metadata.
Author: Gino Bogo

This script provides a user interface to:
- Select an audio file (MP3 or Opus).
- Parse artist and title from the filename.
- Fetch metadata (artist, title, album, year, genre) from MusicBrainz.
- Display current tags of the selected file.
- Allow users to choose from multiple metadata options.
- Apply selected metadata as tags to the audio file.
"""

import base64
import os
import sys
import configparser

import musicbrainzngs

from mutagen._util import MutagenError
from mutagen.flac import Picture
from mutagen.id3 import ID3
from mutagen.id3._frames import TALB, TDRC, TPE1, TIT2, TCON, APIC  # noqa: F401
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QPixmap
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

# Define constants
RIGHT_VCENTER_ALIGNMENT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
NO_FILE_SELECTED_TEXT = "No file selected."
CONFIG_FILE_NAME = "auto_song_tagger.cfg"

# Setup MusicBrainz client
musicbrainzngs.set_useragent("AutoSongTagger", "0.1", "your-email@example.com")

################################################################################


class TagWriterThread(QThread):
    """A QThread subclass to perform tag writing in a separate thread."""

    finished = Signal(bool, str)  # Signal(success, message)
    progress_signal = Signal(str)  # Signal to emit progress messages

    def __init__(self, song_file, metadata, cover_data, parent=None):
        super().__init__(parent)
        self.song_file = song_file
        self.metadata = metadata
        self.cover_data = cover_data

    def run(self):
        try:
            self.progress_signal.emit("Starting tag update...")

            if self.cover_data:
                self.progress_signal.emit("Processing cover art...")

            self.progress_signal.emit("Saving file...")
            write_tags(self.song_file, self.metadata, self.cover_data)
            self.finished.emit(True, "ID3 tags updated successfully!")
        except Exception as e:
            self.finished.emit(False, f"Failed to apply tags: {e}")


class ClickableLabel(QLabel):
    """A QLabel subclass that emits a clicked signal when pressed."""

    clicked = Signal()

    def mousePressEvent(self, event):
        """Handles mouse press events and emits the clicked signal."""
        self.clicked.emit()
        super().mousePressEvent(event)


################################################################################


def fetch_song_metadata(artist, title):
    """Fetches song metadata from MusicBrainz based on artist and title.

    Args:
        artist (str): The artist's name.
        title (str): The song title.

    Returns:
        list: A list of dictionaries, each containing metadata for a recording.
    """
    result = musicbrainzngs.search_recordings(artist=artist, recording=title)
    recordings_metadata = []

    for recording in result["recording-list"]:
        album = ""
        year = ""
        genre = ""

        if "release-list" in recording and recording["release-list"]:
            # Prioritize releases with a date
            releases_with_date = [r for r in recording["release-list"] if "date" in r]

            if releases_with_date:
                # Sort by date to get the earliest or latest, or just pick the
                # first available. For simplicity, pick the first one with a
                # date for now.
                chosen_release = releases_with_date[0]
                album = chosen_release["title"]
                year = chosen_release["date"]
            else:
                # If no release has a date, just take the first release
                chosen_release = recording["release-list"][0]
                album = chosen_release["title"]

        if "tag-list" in recording and recording["tag-list"]:
            # Take the first tag as genre, or concatenate multiple
            genre = ", ".join([tag["name"] for tag in recording["tag-list"]])

        metadata = {
            "title": recording["title"],
            "artist": artist,
            "album": album,
            "year": year,
            "genre": genre,
        }
        recordings_metadata.append(metadata)

    return recordings_metadata


################################################################################


def get_audio_file(file_path: str):
    """Factory function to return the correct mutagen audio object based on file
    extension."""
    _, ext = os.path.splitext(file_path)

    if ext.lower() == ".mp3":
        return MP3(file_path)
    elif ext.lower() == ".opus":
        return OggOpus(file_path)
    else:
        raise MutagenError(f"Unsupported file type: {ext}")


################################################################################


def _write_mp3_tags(audio: MP3, metadata: dict[str, str]):
    """Helper to write MP3 specific tags."""
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

    if metadata.get("genre"):
        audio.tags["TCON"] = TCON(encoding=3, text=[metadata["genre"]])


################################################################################


def _write_ogg_opus_tags(audio: OggOpus, metadata: dict[str, str]):
    """Helper to write OggOpus specific tags."""
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

    if metadata.get("genre"):
        audio.tags["genre"] = metadata["genre"]


################################################################################


def _write_mp3_cover(audio: MP3, cover_data: bytes):
    """Helper to write MP3 cover art."""
    if audio.tags is None:
        audio.tags = ID3()

    # Remove existing APIC frames
    audio.tags.delall("APIC")

    # Add new cover art
    audio.tags.add(
        APIC(
            encoding=3,  # UTF-8
            mime="image/jpeg",  # Assuming JPEG, but could be dynamic
            type=3,  # Front cover
            desc="Cover",
            data=cover_data,
        )
    )


################################################################################


def _write_ogg_opus_cover(audio: OggOpus, cover_data: bytes):
    """Helper to write OggOpus cover art."""
    if audio.tags is None:
        audio.add_tags()

    # Create a Mutagen Picture object
    picture = Picture()
    picture.data = cover_data
    picture.type = 3  # Front cover
    picture.mime = "image/jpeg"  # Assuming JPEG, but could be dynamic

    # Encode the picture to base64 and add to tags
    audio.tags["metadata_block_picture"] = [
        base64.b64encode(picture.write()).decode("ascii")
    ]


################################################################################


def write_tags(
    song_file: str, metadata: dict[str, str], cover_data: bytes | None = None
):
    """Writes tags and optionally cover art to an audio file.

    Args:
        song_file (str): The absolute path to the audio file.
        metadata (dict): A dictionary containing the metadata to write.
        cover_data (bytes, optional): The byte data of the cover image.
                                      Defaults to None.
    """
    try:
        audio = get_audio_file(song_file)
    except MutagenError as e:
        print(f"Error loading {song_file}: {e}")
        raise  # Re-raise the exception to propagate the error

    tag_writers = {
        MP3: _write_mp3_tags,
        OggOpus: _write_ogg_opus_tags,
    }

    writer = tag_writers.get(type(audio))

    if writer:
        writer(audio, metadata)
    else:
        print(f"Unsupported audio type for writing tags: {type(audio)}")
        return

    if cover_data:
        if isinstance(audio, MP3):
            _write_mp3_cover(audio, cover_data)
        elif isinstance(audio, OggOpus):
            _write_ogg_opus_cover(audio, cover_data)
        else:
            print(f"Unsupported audio type for writing cover art: {type(audio)}")

    audio.save()


################################################################################


def parse_artist_title_from_filename(filename):
    """Attempts to parse artist and title from a filename.

    Assumes a format like 'Artist - Title.mp3'.

    Args:
        filename (str): The full path or just the filename of the audio file.

    Returns:
        tuple: A tuple (artist, title) or (None, None) if parsing fails.
    """
    base_name = os.path.splitext(os.path.basename(filename))[0]

    if " - " in base_name:
        parts = base_name.split(" - ", 1)
        artist = parts[0].strip()
        title = parts[1].strip()
        return artist, title

    return None, None


################################################################################


class AutoSongTaggerUI(QWidget):
    def load_settings(self):
        """Loads window size and position from auto_song_tagger.cfg."""
        config = configparser.ConfigParser()
        config_file = CONFIG_FILE_NAME

        if os.path.exists(config_file):
            config.read(config_file)

            if "MainWindow" in config:
                try:
                    x = int(config["MainWindow"]["x"])
                    y = int(config["MainWindow"]["y"])
                    width = int(config["MainWindow"]["width"])
                    height = int(config["MainWindow"]["height"])
                    self.setGeometry(x, y, width, height)
                except ValueError:
                    print("Error reading window geometry from config. Using defaults.")

            if "ColumnWidths" in config:
                try:
                    widths_str = config["ColumnWidths"]["widths"]
                    widths = [int(w) for w in widths_str.split(",")]
                    self._column_widths_from_settings = widths
                except ValueError:
                    print("Error reading column widths from config. Using defaults.")
            else:
                self._column_widths_from_settings = []
        else:
            # Default size and position if no config or error
            self.setGeometry(100, 100, 800, 800)
            self._column_widths_from_settings = []

    ############################################################################

    def apply_column_widths_from_settings(self):
        """Applies column widths from auto_song_tagger.cfg."""
        if getattr(self, "_column_widths_from_settings", []):
            header = self.results_list.horizontalHeader()

            for i, w in enumerate(self._column_widths_from_settings):
                if i < header.count():
                    header.resizeSection(i, w)

    def __init__(self):
        """Initializes the AutoSongTaggerUI application window."""
        super().__init__()
        self.setWindowTitle("Auto Song Tagger")
        self.load_settings()  # Load settings before initializing UI
        self.init_ui()
        self.apply_column_widths_from_settings()  # Apply column widths after UI is initialized
        self._apply_styles()
        self._new_cover_data = None

    def init_ui(self):
        """Initializes the user interface components and layout."""
        main_layout = QVBoxLayout()

        # MP3 File Selection
        file_layout = QHBoxLayout()
        self.file_label = QLabel("Audio File:")
        self.file_label.setFixedWidth(80)
        self.file_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.file_path_input = QLineEdit()
        self.file_path_input.setReadOnly(True)
        self.browse_button = QPushButton("Browse")
        self.browse_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_button.setFixedWidth(100)
        self.browse_button.clicked.connect(self.browse_song_file)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.file_path_input)
        file_layout.addWidget(self.browse_button)
        main_layout.addLayout(file_layout)

        # Artist and Title Input
        input_layout = QHBoxLayout()
        self.artist_label = QLabel("Artist:")
        self.artist_label.setFixedWidth(80)
        self.artist_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.artist_input = QLineEdit()
        self.title_label = QLabel("Title:")
        self.title_label.setFixedWidth(80)
        self.title_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.title_input = QLineEdit()
        input_layout.addWidget(self.artist_label)
        input_layout.addWidget(self.artist_input)
        input_layout.addWidget(self.title_label)
        input_layout.addWidget(self.title_input)
        main_layout.addLayout(input_layout)

        # Action Buttons
        button_layout = QHBoxLayout()
        self.fetch_button = QPushButton("Fetch Metadata")
        self.fetch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_button.clicked.connect(self.fetch_metadata)
        self.apply_button = QPushButton("Apply Tags")
        self.apply_button.setObjectName("applyButton")
        self.apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_button.clicked.connect(self.apply_tags)
        self.apply_button.setEnabled(False)  # Disable until metadata is fetched
        button_layout.addWidget(self.fetch_button)
        button_layout.addWidget(self.apply_button)
        main_layout.addLayout(button_layout)

        # Metadata Results
        self.results_label = QLabel("Metadata Options:")
        self.results_list = QTableWidget()
        self.results_list.setColumnCount(5)  # Artist, Title, Album, Year, Genre
        self.results_list.setHorizontalHeaderLabels(
            ["Artist", "Title", "Album", "Year", "Genre"]
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
        main_layout.addWidget(self.results_label)
        main_layout.addWidget(self.results_list)

        # Current Tags Display
        self.current_tags_label = QLabel("Current Tags:")
        current_tags_input_layout = QVBoxLayout()
        tags_and_cover_layout = QHBoxLayout()

        # Artist
        artist_display_layout = QHBoxLayout()
        self.current_artist_label = QLabel("Artist:")
        self.current_artist_label.setFixedWidth(60)  # Set a fixed width
        self.current_artist_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.current_artist_input = QLineEdit()
        self.current_artist_input.textChanged.connect(self._on_current_tag_text_changed)
        artist_display_layout.addWidget(self.current_artist_label)
        artist_display_layout.addWidget(self.current_artist_input)
        current_tags_input_layout.addLayout(artist_display_layout)

        # Title
        title_display_layout = QHBoxLayout()
        self.current_title_label = QLabel("Title:")
        self.current_title_label.setFixedWidth(60)  # Set a fixed width
        self.current_title_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.current_title_input = QLineEdit()
        self.current_title_input.textChanged.connect(self._on_current_tag_text_changed)
        title_display_layout.addWidget(self.current_title_label)
        title_display_layout.addWidget(self.current_title_input)
        current_tags_input_layout.addLayout(title_display_layout)

        # Album
        album_display_layout = QHBoxLayout()
        self.current_album_label = QLabel("Album:")
        self.current_album_label.setFixedWidth(60)  # Set a fixed width
        self.current_album_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.current_album_input = QLineEdit()
        self.current_album_input.textChanged.connect(self._on_current_tag_text_changed)
        album_display_layout.addWidget(self.current_album_label)
        album_display_layout.addWidget(self.current_album_input)
        current_tags_input_layout.addLayout(album_display_layout)

        # Year
        year_display_layout = QHBoxLayout()
        self.current_year_label = QLabel("Year:")
        self.current_year_label.setFixedWidth(60)  # Set a fixed width
        self.current_year_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.current_year_input = QLineEdit()
        self.current_year_input.textChanged.connect(self._on_current_tag_text_changed)
        year_display_layout.addWidget(self.current_year_label)
        year_display_layout.addWidget(self.current_year_input)
        current_tags_input_layout.addLayout(year_display_layout)

        # Genre
        genre_display_layout = QHBoxLayout()
        self.current_genre_label = QLabel("Genre:")
        self.current_genre_label.setFixedWidth(60)  # Set a fixed width
        self.current_genre_label.setAlignment(RIGHT_VCENTER_ALIGNMENT)
        self.current_genre_input = QLineEdit()
        self.current_genre_input.textChanged.connect(self._on_current_tag_text_changed)
        genre_display_layout.addWidget(self.current_genre_label)
        genre_display_layout.addWidget(self.current_genre_input)
        current_tags_input_layout.addLayout(genre_display_layout)

        tags_and_cover_layout.addLayout(current_tags_input_layout)

        # Disc Cover Placeholder
        self.disc_cover_label = ClickableLabel("Disc Cover")
        self.disc_cover_label.setFixedSize(192, 192)  # Square box
        self.disc_cover_label.setStyleSheet(
            "background-color: #e0e0e0; border: 1px solid #ccc;"
        )
        self.disc_cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.disc_cover_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.disc_cover_label.clicked.connect(self._on_disc_cover_clicked)
        tags_and_cover_layout.addWidget(self.disc_cover_label)

        main_layout.addWidget(self.current_tags_label)
        main_layout.addLayout(tags_and_cover_layout)

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()  # Initially hidden
        main_layout.addWidget(self.progress_bar)

        self.setLayout(main_layout)

    ############################################################################

    def _apply_styles(self):
        """Applies CSS styles to the application.

        Sets the stylesheet for the main application window.
        """
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
                background-color: #FFA500;
                color: #000000;
            }
            QPushButton#applyButton:hover {
                background-color: #E69500;
            }
            QPushButton#applyButton:pressed {
                background-color: #CC8400;
            }
            QPushButton#applyButton:disabled {
                background-color: #B3A180;
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

    ############################################################################

    def save_settings(self):
        """Saves current window size and position to auto_song_tagger.cfg."""
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

    ############################################################################

    def closeEvent(self, event):
        """Overrides the close event to save window settings."""
        self.save_settings()
        event.accept()

    ############################################################################

    def browse_song_file(self):
        """Opens a file dialog to select an MP3 or Opus file and updates the UI."""
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
                    ["Artist", "Title", "Album", "Year", "Genre"]
                )
                self.apply_button.setEnabled(False)

    ############################################################################

    def parse_filename_for_artist_title(self):
        """Parses artist and title from the selected MP3 filename and populates
        the input fields.
        """
        if self.song_file_path:
            artist, title = parse_artist_title_from_filename(self.song_file_path)

            if artist and title:
                self.artist_input.setText(artist)
                self.title_input.setText(title)

    ############################################################################

    def _clear_tag_fields(self, message=None):
        """Clears the tag display fields and optionally shows a message."""
        self.current_artist_input.setText(message or "")
        self.current_title_input.clear()
        self.current_album_input.clear()
        self.current_year_input.clear()
        self.current_genre_input.clear()

    ############################################################################

    def _get_mp3_tag_value(self, tags, tag_name, default="N/A"):
        """Helper to safely get MP3 tag value."""
        if tag_name in tags and tags[tag_name].text:
            return str(tags[tag_name].text[0])
        return default

    ############################################################################

    def _extract_mp3_tags(self, audio):
        """Extracts ID3 tags from an MP3 file."""
        tags = audio.tags
        year = "N/A"

        if "TDRC" in tags and tags["TDRC"].text:
            year_str = str(tags["TDRC"].text[0])

            if len(year_str) >= 4 and year_str[:4].isdigit():
                year = year_str[:4]

        return {
            "artist": self._get_mp3_tag_value(tags, "TPE1"),
            "title": self._get_mp3_tag_value(tags, "TIT2"),
            "album": self._get_mp3_tag_value(tags, "TALB"),
            "year": year,
            "genre": self._get_mp3_tag_value(tags, "TCON"),
        }

    ############################################################################

    def _extract_ogg_tags(self, audio):
        """Extracts tags from an OggOpus file."""
        tags = audio.tags
        return {
            "artist": tags.get("artist", ["N/A"])[0],
            "title": tags.get("title", ["N/A"])[0],
            "album": tags.get("album", ["N/A"])[0],
            "year": tags.get("date", ["N/A"])[0],
            "genre": tags.get("genre", ["N/A"])[0],
        }

    ############################################################################

    def _extract_tags_from_audio(self, audio):
        """Extracts tags from an audio file based on its type."""
        if isinstance(audio, MP3):
            return self._extract_mp3_tags(audio)
        elif isinstance(audio, OggOpus):
            return self._extract_ogg_tags(audio)
        return {}

    ############################################################################

    def _populate_tag_fields(self, tags):
        """Populates the UI fields with the given tags."""
        self.current_artist_input.setText(tags.get("artist", "N/A"))
        self.current_title_input.setText(tags.get("title", "N/A"))
        self.current_album_input.setText(tags.get("album", "N/A"))
        self.current_year_input.setText(tags.get("year", "N/A"))
        self.current_genre_input.setText(tags.get("genre", "N/A"))

    ############################################################################

    def _handle_initial_tag_display_checks(self):
        """Handles initial checks and error conditions for displaying tags."""
        if not self.song_file_path:
            self._clear_tag_fields(NO_FILE_SELECTED_TEXT)
            return None, True  # Return None for audio, True for handled

        try:
            audio = get_audio_file(self.song_file_path)
        except MutagenError:
            self._clear_tag_fields(f"Error loading {self.song_file_path}")
            return None, True

        if not audio.tags:
            self._clear_tag_fields("No tags found.")
            return None, True

        return audio, False  # Return audio object, False for not handled

    ############################################################################

    def display_current_tags(self):
        """Displays the current tags of the selected audio file."""
        audio, handled = self._handle_initial_tag_display_checks()

        if handled:
            return

        tags = self._extract_tags_from_audio(audio)

        if not tags:
            return  # Should not happen

        self._populate_tag_fields(tags)

        # Store original tags for comparison
        self._original_tags = tags
        # Update button state based on initial load
        self._on_current_tag_text_changed()

    ############################################################################

    def _extract_mp3_cover(self, audio):
        """Extracts cover data from an MP3 file."""
        if audio.tags is not None and "APIC:" in audio.tags:
            return audio.tags["APIC:"].data
        return None

    ############################################################################

    def _extract_ogg_opus_cover(self, audio):
        """Extracts cover data from an OggOpus file."""
        if audio.tags is not None and "metadata_block_picture" in audio.tags:
            try:
                cover_data = base64.b64decode(audio.tags["metadata_block_picture"][0])
                picture = Picture(cover_data)
                return picture.data
            except Exception:
                pass
        return None

    ############################################################################

    def display_current_cover(self):
        """Displays the current album cover from the audio file, if available."""
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

        cover_data: bytes | None = None

        if isinstance(audio, MP3):
            cover_data = self._extract_mp3_cover(audio)
        elif isinstance(audio, OggOpus):
            cover_data = self._extract_ogg_opus_cover(audio)

        if cover_data:
            pixmap = QPixmap()
            pixmap.loadFromData(cover_data)
            scaled_pixmap = pixmap.scaled(
                self.disc_cover_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.disc_cover_label.setPixmap(scaled_pixmap)
            self.disc_cover_label.setText("")  # Clear text if image is loaded
        else:
            self.disc_cover_label.clear()
            self.disc_cover_label.setText("No cover found.")

    ############################################################################

    def fetch_metadata(self):
        """Fetches metadata from MusicBrainz based on artist and title input
        fields and populates the results list.
        """
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

        self.results_list.setRowCount(0)  # Clear existing rows
        self.apply_button.setEnabled(False)

        self.metadata_options = fetch_song_metadata(artist, title)

        if not self.metadata_options:
            QMessageBox.information(
                self, "No Results", "No metadata found for this artist and title."
            )
            return

        # Sort by year ascending
        self.metadata_options.sort(
            key=lambda x: (
                int(x.get("year", "9999")[:4])
                if x.get("year", "").strip().isdigit()
                else 9999
            )
        )

        for _, meta in enumerate(self.metadata_options):
            row_position = self.results_list.rowCount()
            self.results_list.insertRow(row_position)

            artist_item = QTableWidgetItem(meta["artist"])
            artist_item.setFlags(artist_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_list.setItem(row_position, 0, artist_item)

            title_item = QTableWidgetItem(meta["title"])
            title_item.setFlags(title_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_list.setItem(row_position, 1, title_item)

            album_item = QTableWidgetItem(meta["album"])
            album_item.setFlags(album_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_list.setItem(row_position, 2, album_item)

            year_item = QTableWidgetItem(meta["year"])
            year_item.setFlags(year_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_list.setItem(row_position, 3, year_item)

            genre_item = QTableWidgetItem(meta["genre"])
            genre_item.setFlags(genre_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.results_list.setItem(row_position, 4, genre_item)

        self.results_list.resizeColumnsToContents()

    ############################################################################

    def enable_apply_button(self):
        """Enables or disables the apply button based on item selection in the
        results list, and populates input fields with selected metadata.
        """
        selected_indexes = self.results_list.selectedIndexes()

        if selected_indexes:
            self.apply_button.setEnabled(True)
            selected_row = selected_indexes[0].row()

            # Get data from the selected row
            artist_item = self.results_list.item(selected_row, 0)
            artist = artist_item.text() if artist_item else ""
            title_item = self.results_list.item(selected_row, 1)
            title = title_item.text() if title_item else ""
            album_item = self.results_list.item(selected_row, 2)
            album = album_item.text() if album_item else ""
            year_item = self.results_list.item(selected_row, 3)
            year = year_item.text() if year_item else ""
            genre_item = self.results_list.item(selected_row, 4)
            genre = genre_item.text() if genre_item else ""

            # Populate current tag display fields
            self.current_artist_input.setText(artist)
            self.current_title_input.setText(title)
            self.current_album_input.setText(album)
            self.current_year_input.setText(year)
            self.current_genre_input.setText(genre)
            # Update button state after populating fields
            self._on_current_tag_text_changed()
        else:
            self.apply_button.setEnabled(False)
            # Clear current tag display fields if nothing is selected
            self.current_artist_input.clear()
            self.current_title_input.clear()
            self.current_album_input.clear()
            self.current_year_input.clear()
            self.current_genre_input.clear()
            # Update button state after clearing fields
            self._on_current_tag_text_changed()

    ############################################################################

    def apply_tags(self):
        """Applies the selected metadata option as tags to the audio file, and
        refreshes the UI to display the updated tags and cover art.
        """
        chosen_metadata = {
            "artist": self.current_artist_input.text(),
            "title": self.current_title_input.text(),
            "album": self.current_album_input.text(),
            "year": self.current_year_input.text(),
            "genre": self.current_genre_input.text(),
        }

        if not chosen_metadata:
            QMessageBox.warning(self, "Error", "No metadata to apply.")
            return

        # Disable apply button to prevent multiple clicks
        self.apply_button.setEnabled(False)

        self.tag_writer_thread = TagWriterThread(
            self.song_file_path, chosen_metadata, self._new_cover_data
        )
        self.tag_writer_thread.finished.connect(self._on_tags_written)
        self.tag_writer_thread.progress_signal.connect(self._on_progress_update)
        self.tag_writer_thread.start()

    def _on_tags_written(self, success, message):
        """Slot to handle the result of the tag writing thread."""
        if success:
            QMessageBox.information(self, "Tags Applied", message)
            self.display_current_tags()
            self.display_current_cover()
            self._new_cover_data = None  # Clear the new cover data after applying
        else:
            QMessageBox.warning(self, "Error", message)

        # Re-enable apply button
        self.apply_button.setEnabled(True)
        self.progress_bar.hide()

    ############################################################################

    def _on_progress_update(self, message):
        """Slot to update the progress bar with messages."""
        self.progress_bar.show()
        self.progress_bar.setFormat(message)
        self.progress_bar.setRange(0, 0)  # Indeterminate progress bar

    ############################################################################

    def _on_current_tag_text_changed(self):
        """Enables the apply button if an audio file is selected and changes are
        detected in any current tag QLineEdit.
        """
        if not self.song_file_path:
            self.apply_button.setEnabled(False)
            return

        current_edited_tags = {
            "artist": self.current_artist_input.text(),
            "title": self.current_title_input.text(),
            "album": self.current_album_input.text(),
            "year": self.current_year_input.text(),
            "genre": self.current_genre_input.text(),
        }

        # Check if any changes have been made to the current tags
        tags_changed = False

        if hasattr(self, "_original_tags"):
            if self._original_tags != current_edited_tags:
                tags_changed = True
        else:
            # If no original tags were loaded, assume changes if fields are not
            # empty

            if any(current_edited_tags.values()):
                tags_changed = True

        # Check if a row is selected in the results list
        row_selected = bool(self.results_list.selectedIndexes())

        # Enable button if tags changed OR a row is selected

        if tags_changed or row_selected:
            self.apply_button.setEnabled(True)
        else:
            self.apply_button.setEnabled(False)

    ############################################################################

    def _on_disc_cover_clicked(self):
        """Handles the click event on the disc cover placeholder, allowing the
        user to select a new cover image.
        """
        file_dialog = QFileDialog(self)
        file_dialog.setNameFilter("Image files (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")

        if file_dialog.exec():
            selected_files = file_dialog.selectedFiles()

            if selected_files:
                image_path = selected_files[0]
                try:
                    with open(image_path, "rb") as f:
                        self._new_cover_data = f.read()

                    pixmap = QPixmap()
                    pixmap.loadFromData(self._new_cover_data)
                    scaled_pixmap = pixmap.scaled(
                        self.disc_cover_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self.disc_cover_label.setPixmap(scaled_pixmap)
                    self.disc_cover_label.setText("")  # Clear text if image is loaded
                except Exception as e:
                    QMessageBox.warning(
                        self, "Error Loading Image", f"Could not load image: {e}"
                    )
                    self._new_cover_data = None


################################################################################

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AutoSongTaggerUI()
    window.show()
    sys.exit(app.exec())
