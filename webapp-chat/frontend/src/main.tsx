import { createRoot } from "react-dom/client";
import "./styles.css";
import { Chat } from "./Chat";
import { Trace } from "./Trace";
import { demoForPath } from "./routes";

const root = document.getElementById("root");
if (!root) throw new Error("Elemento root del frontend non trovato.");
const demo = demoForPath(location.pathname);
createRoot(root).render(location.pathname === "/trace" ? <Trace /> : location.pathname === "/" || demo ? <Chat demo={demo} /> : <main><h1>Pagina non trovata</h1><a href="/">Torna alla chat</a></main>);
