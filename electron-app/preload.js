const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('collab', {
  onMode: (cb) => ipcRenderer.on('mode', (_, m) => cb(m)),
  fetchRecap: async () => {
    try {
      const r = await fetch('http://127.0.0.1:5000/recap');
      const j = await r.json();
      return j.recap || 'No recap available';
    } catch (e) { return 'Backend not running'; }
  }
});
