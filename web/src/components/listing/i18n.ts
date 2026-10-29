// Copy for the public pages (buyer page, disclosure), in English and Spanish. The language toggle switches
// this copy, the film, the room names and the language Q&A answers in.

export type Lang = "en" | "es";

const en = {
  langName: "English",
  sample: "Sample listing",
  otherLang: "Español",
  switchLang: "Ver en español",
  film: "Listing film",
  play: "Play film",
  pause: "Pause film",
  sound: "Turn sound on",
  mute: "Turn sound off",
  rooms: "Rooms",
  jumpTo: (room: string) => `Jump to ${room}`,
  bedsBaths: (b: number | null, ba: number | null) =>
    [b != null ? `${b} bed${b === 1 ? "" : "s"}` : null, ba != null ? `${ba} bath${ba === 1 ? "" : "s"}` : null].filter(Boolean).join(" · "),
  sqft: (n: number) => `${n.toLocaleString("en-US")} sq ft`,
  ask: "Ask about this home",
  askHint: "Answers come from this listing's photos and details. If they can't answer, the listing agent will.",
  askPlaceholder: "Ask about any room",
  askSend: "Ask",
  asking: "Looking through the photos…",
  askError: "That question didn't go through.",
  followUp: "Agent will follow up",
  examples: ["What are the floors like?", "Is there a bathtub?", "What are the HOA fees?"],
  book: "Book a tour",
  bookTitle: "Book a tour",
  bookIntro: "The listing agent gets your request right away and confirms a time with you.",
  name: "Your name",
  contact: "Email or phone",
  when: "When works for you",
  whenPlaceholder: "Saturday morning",
  whenChips: ["This weekend", "Weekday evening", "As soon as possible"],
  send: "Send request",
  sending: "Sending…",
  close: "Close",
  sent: "Request sent",
  sentBody: (when: string) => `The listing agent will confirm “${when}” with you. Your questions from this page went with your request.`,
  done: "Done",
  aiNote: (n: number) =>
    n > 0
      ? n === 1
        ? "One shot in this film uses AI-generated motion and says so on screen."
        : `${n} shots in this film use AI-generated motion and say so on screen.`
      : "Every shot in this film moves through the photo's own pixels. No motion is AI-generated.",
  disclosureLink: "See the original photos and what changed",
  notFound: "This listing isn't available",
  notFoundBody: "It may have been taken down. Ask the listing agent for a new link.",
  retry: "Try again",
  noFilm: "The film for this home is on its way. Here are the photos.",
  // disclosure page
  dTitle: "Photos and film: what changed",
  dIntro:
    "California law (AB 723) asks listings to say when a photo has been altered and to show the original. Each photo below sits next to the camera's original, with every change listed.",
  dOriginal: "Original",
  dPublished: "In the listing",
  dChanges: "What changed",
  dUnchanged: "Not altered.",
  dFilm: "Film: AI-generated motion",
  dFilmNone: "No shot in the film uses AI-generated motion. Every camera move was made from the photo's own pixels.",
  dFilmSome: "These shots were animated by an AI video model. Each passed a check against its photo, and carries the label",
  dRejected: (n: number) => `${n} generated clip${n === 1 ? " was" : "s were"} rejected by the check and ${n === 1 ? "is" : "are"} not in the film.`,
  dShot: (n: number) => `Shot ${n}`,
  dBack: "Back to the listing",
  dKind: {
    fusion: "the camera's exposures fused, before editing",
    exposure: "the camera's middle exposure, before editing",
    delivered: "the photo as the editor delivered it",
    "": "",
  } as Record<string, string>,
  dHdr: (n: number) => `Merged ${n} exposure${n === 1 ? "" : "s"} into one photo (the photo editor's HDR edit).`,
  dOps: {
    restore_from_brackets: "Restored part of the photo from the camera's own exposures",
    regroup: "Merged the exposures from this one camera position into a photo (the editor's version blended two positions)",
    regroup_brackets: "Re-merged the exposures taken from this camera position",
    recolor_region: "Recolored part of the photo",
    adjust_exposure: "Adjusted brightness",
    adjust_white_balance: "Adjusted color balance",
    remove_object: "Removed an object",
    generative_fill: "Filled part of the photo with AI-generated pixels",
    place_on_screen: "Placed an image on a screen",
    add_watermark: "Added a watermark",
    draw_lot_lines: "Drew lot lines",
    revert: "Went back to an earlier version",
  } as Record<string, string>,
  dFix: "Fixed an editing error",
  
};

export type Copy = typeof en;

const es: Copy = {
  langName: "Español",
  sample: "Anuncio de muestra",
  otherLang: "English",
  switchLang: "View in English",
  film: "Video de la propiedad",
  play: "Reproducir video",
  pause: "Pausar video",
  sound: "Activar sonido",
  mute: "Silenciar",
  rooms: "Espacios",
  jumpTo: (room) => `Ir a ${room}`,
  bedsBaths: (b, ba) =>
    [b != null ? `${b} recámara${b === 1 ? "" : "s"}` : null, ba != null ? `${ba} baño${ba === 1 ? "" : "s"}` : null].filter(Boolean).join(" · "),
  sqft: (n) => `${n.toLocaleString("es-MX")} pies²`,
  ask: "Pregunta sobre esta casa",
  askHint: "Las respuestas salen de las fotos y los datos del anuncio. Si no alcanzan, te responde el agente.",
  askPlaceholder: "Pregunta por cualquier espacio",
  askSend: "Preguntar",
  asking: "Revisando las fotos…",
  askError: "No se pudo enviar la pregunta.",
  followUp: "El agente te responderá",
  examples: ["¿Cómo son los pisos?", "¿Hay tina?", "¿Cuánto es la cuota de HOA?"],
  book: "Agenda una visita",
  bookTitle: "Agenda una visita",
  bookIntro: "El agente recibe tu solicitud al instante y confirma la hora contigo.",
  name: "Tu nombre",
  contact: "Correo o teléfono",
  when: "¿Cuándo te queda bien?",
  whenPlaceholder: "Sábado en la mañana",
  whenChips: ["Este fin de semana", "Entre semana, en la tarde", "Lo antes posible"],
  send: "Enviar solicitud",
  sending: "Enviando…",
  close: "Cerrar",
  sent: "Solicitud enviada",
  sentBody: (when) => `El agente confirmará “${when}” contigo. Tus preguntas de esta página van con tu solicitud.`,
  done: "Listo",
  aiNote: (n) =>
    n > 0
      ? n === 1
        ? "Una toma de este video usa movimiento generado por IA y lo indica en pantalla."
        : `${n} tomas de este video usan movimiento generado por IA y lo indican en pantalla.`
      : "Cada toma de este video se mueve con los píxeles de la propia foto. Ningún movimiento es generado por IA.",
  disclosureLink: "Ver las fotos originales y qué cambió",
  notFound: "Este anuncio no está disponible",
  notFoundBody: "Puede que lo hayan retirado. Pide al agente un enlace nuevo.",
  retry: "Reintentar",
  noFilm: "El video de esta casa viene en camino. Aquí están las fotos.",
  dTitle: "Fotos y video: qué cambió",
  dIntro:
    "La ley de California (AB 723) pide indicar cuándo una foto fue alterada y mostrar la original. Cada foto aparece junto a la original de la cámara, con cada cambio anotado.",
  dOriginal: "Original",
  dPublished: "En el anuncio",
  dChanges: "Qué cambió",
  dUnchanged: "Sin alteraciones.",
  dFilm: "Video: movimiento generado por IA",
  dFilmNone: "Ninguna toma del video usa movimiento generado por IA. Cada movimiento de cámara se hizo con los píxeles de la propia foto.",
  dFilmSome: "Estas tomas las animó un modelo de video con IA. Cada una pasó una revisión contra su foto y lleva la etiqueta",
  dRejected: (n) =>
    n === 1
      ? "1 clip generado no pasó la revisión y no aparece en el video."
      : `${n} clips generados no pasaron la revisión y no aparecen en el video.`,
  dShot: (n) => `Toma ${n}`,
  dBack: "Volver al anuncio",
  dKind: {
    fusion: "las exposiciones de la cámara fusionadas, antes de editar",
    exposure: "la exposición central de la cámara, antes de editar",
    delivered: "la foto tal como la entregó el editor",
    "": "",
  },
  dHdr: (n) => `Se combinaron ${n} exposicion${n === 1 ? "" : "es"} en una sola foto (la edición HDR del editor de fotos).`,
  dOps: {
    restore_from_brackets: "Se restauró parte de la foto con las exposiciones de la propia cámara",
    regroup: "Se combinaron en una foto las exposiciones de esta sola posición de cámara (la versión del editor mezclaba dos posiciones)",
    regroup_brackets: "Se volvieron a combinar las exposiciones de esta posición de cámara",
    recolor_region: "Se cambió el color de parte de la foto",
    adjust_exposure: "Se ajustó el brillo",
    adjust_white_balance: "Se ajustó el balance de color",
    remove_object: "Se quitó un objeto",
    generative_fill: "Se rellenó parte de la foto con píxeles generados por IA",
    place_on_screen: "Se colocó una imagen en una pantalla",
    add_watermark: "Se agregó una marca de agua",
    draw_lot_lines: "Se dibujaron los límites del terreno",
    revert: "Se volvió a una versión anterior",
  },
  dFix: "Se corrigió un error de edición",

};

export const COPY: Record<Lang, Copy> = { en, es };

const KEY = "shootagent.buyer.lang";

export function initialLang(): Lang {
  const saved = localStorage.getItem(KEY);
  if (saved === "en" || saved === "es") return saved;
  return navigator.language.toLowerCase().startsWith("es") ? "es" : "en";
}

export function saveLang(lang: Lang) {
  localStorage.setItem(KEY, lang);
  document.documentElement.lang = lang;
}
