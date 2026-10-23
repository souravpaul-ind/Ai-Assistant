# 🤖 Ai-Assistant

**A personal AI assistant for Termux on Android, powered by OpenRouter.**

Ai-Assistant brings AI-powered chat, workspace file management, shell command execution, and optional voice interaction to your Termux terminal.

## ✨ Features

* 🤖 AI chat powered by OpenRouter
* 📁 Create, read, edit, list, and delete workspace files
* 💻 Execute shell commands with user confirmation
* 📂 Work in the directory from which the assistant is launched
* 🛡️ Workspace path restrictions for file operations
* 🎨 Hacker-style terminal welcome screen
* 🕒 Display the date, time, and active workspace
* 🎙️ Voice input using `/listen`
* 🔊 Spoken AI replies using `/speak on` and `/speak off`
* 🧰 Start a local Python HTTP server
* ⚙️ Process management with confirmation
* 🔐 Store the OpenRouter API key locally

## 📋 Requirements

* Android device
* [Termux](https://github.com/termux/termux-app)
* Python 3
* Git
* An [OpenRouter API key](https://openrouter.ai/keys)
* Termux:API app and package for optional voice features

## ⚡ Quick Installation

### Step 1: Install the required packages

Open Termux and run:

```bash
pkg update
pkg install python git -y
```

### Step 2: Clone the repository

```bash
git clone https://github.com/souravpaul-ind/Ai-Assistant.git
cd Ai-Assistant
```

### Step 3: Run the setup script

```bash
bash setup.sh
```

Follow the installer's on-screen instructions. If the installer asks for an OpenRouter API key, enter it when prompted.

Get your API key from:

**https://openrouter.ai/keys**

The intended local key file is:

```text
~/.config/openrouter/key
```

The key should remain private and must never be committed to GitHub. If you already have a configured key, the installer should preserve it rather than unnecessarily asking for it again.

### Step 4: Start the assistant

After installation, run:

```bash
ai
```

To use the assistant with a particular project, first navigate to that directory:

```bash
cd ~/my-project
ai
```

Replace `~/my-project` with the directory you want the assistant to work in.

If the `ai` command is unavailable, check whether the installation completed successfully and whether `$PREFIX/bin` is in your Termux `PATH`.

## 🔑 OpenRouter API Key

Ai-Assistant uses OpenRouter to communicate with AI models.

1. Visit https://openrouter.ai/keys.
2. Create an API key.
3. Enter the key when requested by the installer, if supported by your version of `setup.sh`.
4. Keep the key in the local configuration file.

Configuration path:

```text
~/.config/openrouter/key
```

To secure the file permissions:

```bash
chmod 600 ~/.config/openrouter/key
```

**Security reminders:**

* Never publish your API key.
* Never add the key to source code or README files.
* Never commit the key to a public repository.
* Review commands before approving them.

## 🎙️ Optional Voice Setup

Voice features require the Termux:API Android app and its Termux package.

Install the package:

```bash
pkg install termux-api -y
```

Install the compatible Termux:API app, grant the required Android permissions, and ensure that Android speech recognition and text-to-speech services are available.

### Voice Commands

| Command      | Description                                  |
| ------------ | -------------------------------------------- |
| `/listen`    | Recognize speech and send the text to the AI |
| `/speak on`  | Enable spoken AI replies                     |
| `/speak off` | Disable spoken AI replies                    |

When voice replies are enabled, the assistant attempts to speak AI responses using Termux text-to-speech. Use `/speak off` to return to text-only replies.

## 💬 Available Commands

| Command      | Description                            |
| ------------ | -------------------------------------- |
| `/help`      | Display available commands             |
| `/listen`    | Capture voice input                    |
| `/speak on`  | Enable spoken AI replies               |
| `/speak off` | Disable spoken AI replies              |
| `/clear`     | Clear the current conversation history |
| `/exit`      | Exit the assistant                     |

## 🧰 Tools

Depending on the installed version, the assistant can provide tools for:

* Listing files and directories
* Reading text files
* Creating and editing files
* Deleting files and directories
* Creating directories
* Running shell commands
* Starting a local Python HTTP server
* Stopping a process by PID

Operations that modify files, execute commands, or affect processes should require user confirmation.

Always review the requested operation before approving it. Workspace restrictions are a safety measure, not a substitute for reviewing commands.

## 📂 Project Structure

```text
Ai-Assistant/
├── assistant.py    # Main AI agent and tools
├── ui.py           # Terminal interface
├── voice.py        # Optional voice helpers
├── setup.sh        # Termux installation script
└── README.md       # Project documentation
```

## 🛠️ Troubleshooting

### The `ai` command is not found

Check whether the setup script completed successfully and whether the launcher exists:

```bash
ls "$PREFIX/bin/ai"
```

If the file is missing, review the output from `bash setup.sh`.

### OpenRouter API key error

Verify that the key is valid and that the configuration file exists:

```bash
cat ~/.config/openrouter/key
```

**Do not share the output of this command.** It displays your secret API key.

If needed, edit the file locally:

```bash
nano ~/.config/openrouter/key
chmod 600 ~/.config/openrouter/key
```

### Voice input is not working

* Confirm that the Termux:API app is installed.
* Confirm that the `termux-api` package is installed in Termux.
* Grant the required microphone permissions.
* Check that Android speech recognition is available.

### Text-to-speech is not working

* Confirm that Termux:API is installed and accessible.
* Check Android text-to-speech settings.
* Test whether the `termux-tts-speak` command is available.

### A Python module is missing

Review the setup output and install the missing dependency using the appropriate Termux package or Python package installer.

### File access is blocked

The assistant restricts file tools to its active workspace. Launch it from the directory you intend to work in and verify that the requested path is inside that workspace.

## 🔒 Security

Ai-Assistant can execute commands and modify files, so use it responsibly.

* Review every command before approving it.
* Avoid running unfamiliar or destructive commands.
* Keep API keys and other secrets outside public repositories.
* Do not assume workspace restrictions make every shell command safe.
* Back up important files before allowing automated modifications.

## 🔄 Updating

To update an existing installation, follow the project's documented update procedure. Back up local changes before replacing any files.

Do not overwrite local configuration or API key files when updating.

## 📄 License

Check the repository for the project's license and usage terms.

---

**Made for Termux users who want an AI assistant in their terminal.** 🚀

