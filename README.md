# AutoSongTagger

## Description
AutoSongTagger is a Python GUI application designed to automatically tag and organize your music collection. It leverages metadata from MusicBrainz and other public music APIs (TheAudioDB, Deezer, Lyrics.ovh), and provides a user-friendly interface to fetch, select, and apply accurate information to your audio files (MP3 and Opus), making your music library easily searchable and browsable.

## Features
- **GUI Application:** User-friendly interface for tagging audio files.
- **File Selection:** Allows selection of MP3 and Opus audio files.
- **Filename Parsing:** Automatically parses artist and title from filenames (e.g., "Artist - Title.mp3").
- **Metadata Integration:** Fetches comprehensive metadata (artist, title, album, year, genre) from MusicBrainz, TheAudioDB, Deezer, and Lyrics.ovh.
- **Current Tag Display:** Displays existing tags and album cover art of the selected file.
- **Metadata Options:** Presents multiple metadata suggestions from MusicBrainz for user selection.
- **Tag Application:** Applies selected metadata as tags to the audio file.
- **Supported Formats:** MP3 and Ogg Opus.

![figure_01.png](docs/images/figure_01.png)

## Installation

### Prerequisites
- Python 3.x
- PySide6
- musicbrainzngs
- mutagen

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/GinoBogo/AutoSongTagger.git
   cd AutoSongTagger
   ```
2. Create a virtual environment (recommended):
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
   ```
3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage
To run AutoSongTagger, navigate to the project directory and execute the script:

```bash
python auto_song_tagger.py
```

This will launch the graphical user interface where you can select audio files, fetch metadata, and apply tags.

## License
This project is licensed under the MIT License - see the `LICENSE` file for details.