// Framework7 (iOS theme) renders everything inside the glass. Its CSS is imported in src/index.css,
// in a cascade layer below Tailwind's.
import Framework7 from "framework7/lite";
import Framework7React from "framework7-react";
import InputComponent from "framework7/components/input";
import MessagesComponent from "framework7/components/messages";
import MessagebarComponent from "framework7/components/messagebar";

Framework7.use([Framework7React, InputComponent, MessagesComponent, MessagebarComponent]);
