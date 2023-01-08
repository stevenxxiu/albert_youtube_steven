# Albert Launcher YouTube Extension
## Install
To install, copy or symlink this directory to `~/.local/share/albert/org.albert.extension.python/modules/youtube_steven/`.

## Development Setup
To setup the project for development, run:

    $ cd youtube_steven/
    $ pre-commit install --hook-type pre-commit --hook-type commit-msg

To lint and format files, run:

    $ pre-commit run --all-files
