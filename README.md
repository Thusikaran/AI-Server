# AI Monitor Server: Intelligent Surveillance System

## 📖 About the Project
This project is an advanced AI-powered surveillance system. It provides real-time video streaming, state-of-the-art object detection (YOLO/RT-DETR), and a robust backend to monitor environments and track activities across different shops and cameras.

### Key Features
- **Real-time Camera Management**: Add, configure, and monitor multiple RTSP streams grouped by Shop.
- **AI Motion & Object Detection**: Built-in background workers that process streams for motion, humans, weapons, fire, and custom models.
- **Cloud Theft AI Trigger**: Selectively arm cameras with a Theft AI mode that automatically pings an external AWS/Cloud endpoint when motion is detected.
- **Event Timeline**: A 24-hour visual timeline for each camera showing exactly when motion or AI detections occurred.
- **Interactive Dashboard**: Modern glassmorphism UI for viewing camera statuses, bulk-importing setups, and configuring system settings.

---

## 🛠 Prerequisites: Installing Docker on Ubuntu

The easiest way to run this project is using Docker and Docker Compose. If you don't have them installed on your Ubuntu machine, run the following commands in your terminal:

```bash
# 1. Update your package list and install prerequisites
sudo apt-get update
sudo apt-get install ca-certificates curl gnupg -y

# 2. Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# 3. Add the Docker repository to Apt sources
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 4. Install Docker Engine and Docker Compose
sudo apt-get update
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin -y

# 5. Add your user to the docker group (avoids needing 'sudo' for docker commands)
sudo usermod -aG docker $USER
# NOTE: Log out and log back in, or run `newgrp docker` for this to take effect.
```

---

## 🚀 Running the Project via Docker (Recommended)

1. **Environment Setup**
   Ensure you have a `.env` file in the root directory (alongside `docker-compose.yml`). Example:
   ```env
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=aimonitor
   DATABASE_URL=postgresql://postgres:postgres@db:5432/aimonitor
   SECRET_KEY=supersecretkey
   HF_TOKEN=your_huggingface_token
   ```

2. **Build and Run**
   Navigate to the root directory and run:
   ```bash
   docker compose up -d --build
   ```
   This will build the Python backend, set up the PostgreSQL database, initialize MediaMTX, and serve the Nginx frontend.

3. **Access the Application**
   - **Frontend (Dashboard):** [http://localhost](http://localhost)
   - **Backend API Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)
   - **RTSP Stream Server:** `rtsp://localhost:8554`

4. **Stopping the Server**
   ```bash
   docker compose down
   ```

---

## 💻 Running the Project Locally (Without Docker)

If you prefer to run the backend natively on your Ubuntu machine (for active development or debugging), follow these steps:

### 1. Install System Dependencies
The backend requires Python, PostgreSQL, and system libraries for OpenCV.
```bash
sudo apt update
sudo apt install python3-pip python3-venv postgresql postgresql-contrib libgl1-mesa-glx -y
```

### 2. Setup the Database
Create the database in PostgreSQL:
```bash
sudo -u postgres psql -c "CREATE DATABASE aimonitor;"
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD 'postgres';"
```

### 3. Install Python Dependencies
Navigate into the `backend/` folder, create a virtual environment, and install dependencies:
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Run the Backend
With your virtual environment active and `.env` configured:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

*(Note: If running natively, you will also need to manually serve the `frontend/` folder using a simple HTTP server or Nginx, and run a local instance of MediaMTX if you require RTSP passthrough).*

---

## ⚙️ Configuring System Settings (UI)
The system features a **Settings UI** to manage global configurations (like the Cloud Theft API Trigger URL). 
- Go to the **Shops & Cameras** tab.
- Click the **⚙️ Settings** button in the top right.
- Set the webhook URL that should be triggered upon motion detection.
