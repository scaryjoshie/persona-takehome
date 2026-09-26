import { createRoot } from "react-dom/client";
import { IPhone17Pro } from "./IPhone17Pro";
import { MessagesScreen } from "./MessagesScreen";

const messages = [
  { id: "1", side: "received", text: "Hey! This is Persona. Thanks for signing up 👋" },
  { id: "2", side: "received", text: "Mind if I ask a couple of quick questions to get you set up?" },
  { id: "3", side: "sent", text: "Sure, go ahead" },
  { id: "4", side: "received", text: "What should I call you?" },
  { id: "5", side: "sent", text: "Joshua" },
  { id: "6", side: "sent", text: "And I'm usually free after 6pm if a call is easier" },
] as const;

const keyboard = new URLSearchParams(location.search).has("keyboard");

createRoot(document.getElementById("root")!).render(
  <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#161a22" }}>
    <IPhone17Pro>
      <MessagesScreen contact="Persona" messages={[...messages]} typing keyboard={keyboard} />
    </IPhone17Pro>
  </div>,
);
