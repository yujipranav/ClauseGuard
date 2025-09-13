const { app, BrowserWindow, globalShortcut } = require('electron');

let popup;
function createPopup() {
  popup = new BrowserWindow({
    width: 420,
    height: 260,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    show: false,
    webPreferences: { preload: __dirname + '/preload.js' }
  });
  popup.loadFile('index.html');
}

app.whenReady().then(() => {
  createPopup();
  globalShortcut.register('CommandOrControl+Shift+R', () => { popup.show(); popup.webContents.send('mode', 'recap'); });
  globalShortcut.register('CommandOrControl+Shift+S', () => { popup.show(); popup.webContents.send('mode', 'safeshare'); });
});

app.on('will-quit', () => { globalShortcut.unregisterAll(); });
