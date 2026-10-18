import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { DemoProvider } from "./state/DemoProvider";
import { App } from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <HashRouter>
    <DemoProvider>
      <App />
    </DemoProvider>
  </HashRouter>,
);
