const { contextBridge, ipcRenderer } = require('electron');

console.log('[preload] loaded');

contextBridge.exposeInMainWorld('collab', {
  // renderer -> main
  setMode: (mode) => ipcRenderer.invoke('ui:mode', mode),
  hide: () => ipcRenderer.invoke('window:hide'),
  quit: () => ipcRenderer.invoke('window:quit'),

  // main -> renderer
  onMode: (cb) => ipcRenderer.on('mode', (_e, m) => cb(m)),
  onSafeData: (cb) => ipcRenderer.on('safe-data', (_e, items) => cb(items)),
  onSafeError: (cb) => ipcRenderer.on('safe-error', (_e, msg) => cb(msg)),

  // backend
  fetchRecap: async () => {
    try {
      const r = await fetch('http://127.0.0.1:5000/recap');
      const j = await r.json();
      return j.recap || 'No recap available';
    } catch {
      return 'Backend not running';
    }
  }
});







