from ui import ManifestDownloaderUI

if __name__ == "__main__":
    app = ManifestDownloaderUI()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()