const { app, BrowserWindow, globalShortcut, desktopCapturer, ipcMain } = require('electron');
const path = require('path');

let win;

function createWindow() {
  win = new BrowserWindow({
    width: 560,
    height: 460,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    show: false, // shown via hotkeys or tab click
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  win.loadFile(path.join(__dirname, 'index.html'));

  // Log renderer issues
  win.webContents.on('did-fail-load', (_e, code, desc) => {
    console.error('[did-fail-load]', code, desc);
  });
  win.webContents.on('render-process-gone', (_e, d) => {
    console.error('[renderer-gone]', d);
  });
}

async function sendSafeShare() {
  try {
    const sources = await desktopCapturer.getSources({
      types: ['window', 'screen'],
      thumbnailSize: { width: 340, height: 220 }
    });
    const payload = sources.slice(0, 10).map(s => ({
      id: s.id,
      name: s.name,
      thumb: s.thumbnail.toDataURL()
    }));
    win.webContents.send('safe-data', payload);
  } catch (err) {
    win.webContents.send('safe-error', String(err));
  }
}

function registerHotkeys() {
  globalShortcut.register('Control+Shift+R', () => {
    if (!win.isVisible()) win.show(); else win.focus();
    win.webContents.send('mode', 'recap');
  });

  globalShortcut.register('Control+Shift+S', async () => {
    if (!win.isVisible()) win.show(); else win.focus();
    win.webContents.send('mode', 'safeshare');
    await sendSafeShare();
  });

  // DevTools toggle for debugging
  globalShortcut.register('Control+Alt+I', () => win.webContents.toggleDevTools());
}

app.whenReady().then(() => {
  createWindow();
  registerHotkeys();
});

// Renderer -> Main
ipcMain.handle('ui:mode', async (_e, mode) => {
  if (!win) return;
  if (!win.isVisible()) win.show(); else win.focus();
  win.webContents.send('mode', mode);
  if (mode === 'safeshare') await sendSafeShare(); // refresh thumbnails on tab open
});

ipcMain.handle('window:hide', () => { if (win) win.hide(); });
ipcMain.handle('window:quit', () => app.quit());

app.on('will-quit', () => globalShortcut.unregisterAll());



