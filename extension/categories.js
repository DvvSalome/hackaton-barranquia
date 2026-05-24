// Mapa dominio → categoría. El backend tiene la lista completa, este
// fichero replica un subset para que el popup pueda mostrar la categoría
// en tiempo real. Mantener en sync con api-service/digital.py.

export const CATEGORY_MAP = {
  // social
  'instagram.com': 'social',
  'tiktok.com': 'social',
  'twitter.com': 'social',
  'x.com': 'social',
  'facebook.com': 'social',
  'reddit.com': 'social',
  'snapchat.com': 'social',
  'threads.net': 'social',
  'bsky.app': 'social',
  'pinterest.com': 'social',
  'linkedin.com': 'social',

  // entretenimiento
  'youtube.com': 'entertainment',
  'youtu.be': 'entertainment',
  'netflix.com': 'entertainment',
  'twitch.tv': 'entertainment',
  'primevideo.com': 'entertainment',
  'disneyplus.com': 'entertainment',
  'spotify.com': 'entertainment',
  'soundcloud.com': 'entertainment',

  // news
  'elpais.com': 'news', 'eltiempo.com': 'news', 'semana.com': 'news',
  'bbc.com': 'news', 'cnn.com': 'news', 'nytimes.com': 'news',
  'elheraldo.co': 'news', 'infobae.com': 'news',

  // work
  'mail.google.com': 'work', 'gmail.com': 'work',
  'outlook.com': 'work', 'outlook.office.com': 'work',
  'slack.com': 'work', 'notion.so': 'work', 'trello.com': 'work',
  'asana.com': 'work', 'atlassian.net': 'work', 'jira.com': 'work',
  'github.com': 'work', 'gitlab.com': 'work', 'linear.app': 'work',
  'figma.com': 'work',
  'docs.google.com': 'work', 'drive.google.com': 'work',
  'calendar.google.com': 'work',

  // education
  'wikipedia.org': 'education', 'stackoverflow.com': 'education',
  'coursera.org': 'education', 'udemy.com': 'education',
  'khanacademy.org': 'education', 'developer.mozilla.org': 'education',
  'edx.org': 'education', 'scholar.google.com': 'education',
  'arxiv.org': 'education',

  // shopping
  'amazon.com': 'shopping', 'mercadolibre.com.co': 'shopping',
  'mercadolibre.com': 'shopping', 'ebay.com': 'shopping',
  'aliexpress.com': 'shopping', 'shein.com': 'shopping',

  // search
  'google.com': 'search', 'bing.com': 'search',
  'duckduckgo.com': 'search', 'perplexity.ai': 'search', 'kagi.com': 'search',

  // ai
  'chat.openai.com': 'ai', 'chatgpt.com': 'ai', 'claude.ai': 'ai',
  'gemini.google.com': 'ai', 'bard.google.com': 'ai',
  'copilot.microsoft.com': 'ai', 'poe.com': 'ai',
};

export function normalizeDomain(urlOrDomain) {
  if (!urlOrDomain) return '';
  let host;
  try {
    host = urlOrDomain.includes('://')
      ? new URL(urlOrDomain).hostname
      : urlOrDomain.split('/')[0];
  } catch {
    host = urlOrDomain;
  }
  host = (host || '').toLowerCase().trim();
  if (host.startsWith('www.')) host = host.slice(4);
  return host;
}

export function categorize(domainOrUrl) {
  const d = normalizeDomain(domainOrUrl);
  if (!d) return 'other';
  if (CATEGORY_MAP[d]) return CATEGORY_MAP[d];
  for (const known in CATEGORY_MAP) {
    if (d.endsWith('.' + known)) return CATEGORY_MAP[known];
  }
  return 'other';
}

// Motores de búsqueda con su parámetro de query.
export const SEARCH_ENGINES = [
  { match: /(^|\.)google\./, name: 'google', param: 'q' },
  { match: /(^|\.)bing\.com$/, name: 'bing', param: 'q' },
  { match: /(^|\.)duckduckgo\.com$/, name: 'duckduckgo', param: 'q' },
  { match: /(^|\.)perplexity\.ai$/, name: 'perplexity', param: 'q' },
  { match: /(^|\.)kagi\.com$/, name: 'kagi', param: 'q' },
];

export function detectSearch(urlString) {
  try {
    const u = new URL(urlString);
    const host = u.hostname.replace(/^www\./, '');
    for (const eng of SEARCH_ENGINES) {
      if (eng.match.test(host)) {
        const q = (u.searchParams.get(eng.param) || '').trim();
        if (q) return { engine: eng.name, query: q };
      }
    }
  } catch {
    // ignore
  }
  return null;
}
