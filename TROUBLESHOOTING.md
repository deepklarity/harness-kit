# Troubleshooting Guide

This guide provides solutions to common issues you might encounter while using Harness-Kit. If you run into problems, refer to the sections below for troubleshooting steps.

## Common Issues
### 1. Installation Issues
- **Problem**: Errors during installation of dependencies.
- **Solution**: Ensure you have Python 3.12 or higher installed. Check that you have `pip` and `pipx` installed and properly configured. You can also try creating a virtual environment manually and installing dependencies there.

### 2. Odin CLI Issues
- **Problem**: Errors when running Odin CLI commands.
- **Solution**: Make sure you have installed the Odin CLI tool globally using `pipx`. Verify that the installation was successful by running `odin doctor`.

### 3. Do not have Claude Code or Codex
- **Problem**: Errors related to missing Claude Code or Codex.
- **Solution**: We find plan mode to be stable only in these tools. However, if you want to try with other harnesses, please modify .odin/config.yaml (on your Project's directory) to the harness you want to use. `base_agent: claude` to something else.

### 4. Odin plan opened empty tmux
- **Problem**: When you run `odin plan`, it opens an empty tmux session.
- **Solution**: This is a known bug. Try closing session normally and running plan again.

If you have tried above solutions and still facing issues, please raise a github issue with detailed description and logs if possible. We will try to help as soon as we can.