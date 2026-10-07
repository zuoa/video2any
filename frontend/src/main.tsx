import React from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import { App } from "./App";
import "./styles.css";
import "./ui.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Theme className="app-theme" appearance="light" accentColor="violet" grayColor="mauve" radius="large" panelBackground="solid">
      <App />
    </Theme>
  </React.StrictMode>
);
