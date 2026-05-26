chrome.runtime.onMessage.addListener(async (request, sender, sendResponse) => {
  if (request.action === "EXTRACT_GOG") {
    try {
      // Chiama l'endpoint interno di GOG usando la sessione sicura già attiva dell'utente
      const res = await fetch('/account/getFilteredProducts?hiddenFlag=0&mediaType=1&page=1&sortBy=title');
      const data = await res.json();
      
      const parsedGames = data.products.map(p => {
        let coverUrl = p.image ? 'https:' + p.image : null;
        return { name: p.title, cover: coverUrl, url: "https://www.gog.com" + p.url };
      });

      // Spedisce i dati alla Web App eliminando ogni interazione con file locali o tasti strani
      window.opener.postMessage({ type: "GOG_EXT_DATA", payload: parsedGames }, "*");
      alert("Sincronizzazione completata! Torna sulla scheda della Web App.");
    } catch (e) {
      alert("Errore durante la sincronizzazione. Assicurati di aver effettuato l'accesso su GOG.");
    }
  }
});