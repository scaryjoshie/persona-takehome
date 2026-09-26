import { Icon } from "framework7-react";

function Avatar({ name, size }: { name: string; size: number }) {
  return (
    <span className="contact-card-avatar" style={{ width: size, height: size, fontSize: size * 0.45 }}>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

/** The agent's contact, sent as an attachment: as Messages shows a shared .vcf. Tapping saves it. */
export function ContactCardBubble({ name, saved, onSave }: { name: string; saved: boolean; onSave?: () => void }) {
  return (
    <button type="button" className="contact-card" onClick={onSave} disabled={saved || !onSave}>
      <span className="contact-card-row">
        <Avatar name={name} size={40} />
        <span className="contact-card-name">{name}</span>
        <Icon f7="chevron_right" className="contact-card-chevron" />
      </span>
      <span className="contact-card-action">{saved ? "Saved to Contacts" : "Add to Contacts"}</span>
    </button>
  );
}

/** iOS 17's Name and Photo Sharing prompt, pinned under the header until the user updates the contact. */
export function ContactBanner({ name, onUpdate }: { name: string; onUpdate: () => void }) {
  return (
    <div className="contact-banner" role="status">
      <Avatar name={name} size={30} />
      <span className="contact-banner-text">
        <b>{name}</b> updated their name
      </span>
      <button type="button" onClick={onUpdate}>
        Update
      </button>
    </div>
  );
}
