//! Meridian 桌面壳（Tauri 2）。
//!
//! 职责最小化：窗口加载本地 Meridian 服务（http://127.0.0.1:8300）；
//! 启动时若服务未监听则拉起 `python -m uvicorn meridian.webapp:app`（项目 venv 优先），
//! 退出时回收后端进程。所有量化/AI 逻辑都在服务端——壳里零业务逻辑。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// 后端子进程句柄（退出时 kill）。
struct Backend(Mutex<Option<Child>>);

impl Drop for Backend {
    fn drop(&mut self) {
        if let Some(child) = self.0.lock().unwrap().as_mut() {
            let _ = child.kill();
        }
    }
}

fn service_up(port: u16) -> bool {
    TcpStream::connect(("127.0.0.1", port)).is_ok()
}

/// 依次探测候选 python（venv 优先，回退 PATH），返回第一个存在的。
fn find_python() -> Option<std::path::PathBuf> {
    let candidates = [
        // 开发环境：项目 venv（main.rs 编译期相对 src-tauri，运行期用 CWD 锚定）
        std::env::current_exe().ok().and_then(|p| {
            p.ancestors().nth(3).map(|root| {
                root.join(".venv").join("Scripts").join("python.exe")
            })
        }),
        Some(std::path::PathBuf::from("python")),
    ];
    for c in candidates.into_iter().flatten() {
        if c.is_absolute() {
            if c.exists() {
                return Some(c);
            }
        } else if Command::new(&c).arg("--version").output().is_ok() {
            return Some(c);
        }
    }
    None
}

fn spawn_backend(port: u16) -> Option<Child> {
    let python = find_python()?;
    // CWD 定位项目根（.venv 同级）；找不到则用当前目录
    let root = std::env::current_exe()
        .ok()
        .and_then(|p| p.ancestors().nth(3).map(|r| r.to_path_buf()))
        .unwrap_or_else(|| std::path::PathBuf::from("."));
    let mut cmd = Command::new(&python);
    cmd.args(["-m", "uvicorn", "meridian.webapp:app", "--host", "127.0.0.1", "--port", &port.to_string()])
        .current_dir(&root);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW：不闪黑框
    }
    cmd.spawn().ok()
}

fn main() {
    let port: u16 = 8300;
    let backend = Backend(Mutex::new(None));

    if !service_up(port) {
        if let Some(child) = spawn_backend(port) {
            *backend.0.lock().unwrap() = Some(child);
        }
        // 等后端就绪（最多 20s），不阻塞失败——窗口自身会显示连接错误
        let deadline = Instant::now() + Duration::from_secs(20);
        while Instant::now() < deadline && !service_up(port) {
            std::thread::sleep(Duration::from_millis(300));
        }
    }

    tauri::Builder::default()
        .manage(backend)
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
