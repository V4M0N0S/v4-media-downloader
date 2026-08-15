# Changelog

## v1.0.0

Initial public release of V4 Media Downloader.

### Features
- YouTube video and audio downloads
- Twitch Clips and VOD support
- MP3 and MP4 output
- Selectable video quality
- Media preview with thumbnail, title, creator and duration
- Optional start and end time for VOD downloads
- Direct YouTube search
- TXT bulk downloads
- Live download progress and queue
- Media library with thumbnails and preview
- Download and delete individual files
- Download All and Delete All
- Discord webhook notifications
- Optional YouTube cookie authentication
- German, English and Spanish interface
- Extensible JSON-based localization
- Responsive Material-inspired web interface
- systemd service
- Automated installation and update scripts

### Server Structure
- Application: `/opt/v4-media-downloader`
- Configuration: `/etc/v4-media-downloader`
- Downloads: `/var/lib/v4-media-downloader/downloads`
- Thumbnails: `/var/lib/v4-media-downloader/thumbnails`
- Temporary files: `/var/lib/v4-media-downloader/work`