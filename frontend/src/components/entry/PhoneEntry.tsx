import { useState, type FormEvent } from "react";
import { ArrowUp } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { OTPInput, REGEXP_ONLY_DIGITS, type SlotProps } from "input-otp";

const DIGITS = 10;

interface Props {
  /** Prefills the blanks, e.g. the number used last time. */
  initial?: string;
  onSubmit: (digits: string) => void;
}

function Slot({ char, isActive, hasFakeCaret }: SlotProps) {
  return (
    <div className="relative flex h-14 w-8 items-end justify-center pb-2">
      <span className="text-4xl font-light tabular-nums text-white">{char}</span>
      {hasFakeCaret && <span className="absolute bottom-3 h-8 w-px animate-pulse bg-white" />}
      <span
        className={`absolute inset-x-0.5 bottom-0 h-px transition-colors ${isActive ? "bg-white" : "bg-white/25"}`}
      />
    </div>
  );
}

const Punct = ({ children }: { children: string }) => (
  <span className="pb-2 text-4xl font-light text-white/35">{children}</span>
);

/**
 * The first screen: everything behind it frosted, ten blanks that fill as you type (input-otp, MIT),
 * and a blue arrow that appears once the number is complete.
 */
export function PhoneEntry({ initial = "", onSubmit }: Props) {
  const [value, setValue] = useState(initial);
  const complete = value.length === DIGITS;

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (complete) onSubmit(value);
  };

  return (
    <motion.form
      onSubmit={submit}
      initial={{ opacity: 0, backdropFilter: "blur(0px)" }}
      animate={{ opacity: 1, backdropFilter: "blur(40px)" }}
      exit={{ opacity: 0, backdropFilter: "blur(0px)", transition: { duration: 0.9, ease: [0.16, 1, 0.3, 1] } }}
      transition={{ duration: 0.6 }}
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-10 bg-black/40"
    >
      <motion.div className="text-center" exit={{ y: -12, opacity: 0, transition: { duration: 0.35 } }}>
        <h1 className="text-2xl font-medium text-white">Enter your phone number</h1>
        <p className="mt-2 text-sm text-neutral-400">It's how Persona knows it's you.</p>
      </motion.div>

      <div className="flex items-end gap-4">
        <OTPInput
          autoFocus
          maxLength={DIGITS}
          value={value}
          onChange={setValue}
          // input-otp selects the last digit when the field fills; collapse that to a plain caret so
          // nothing (browser or extension) decorates a selection inside the invisible input.
          onComplete={() =>
            requestAnimationFrame(() => {
              const input = document.activeElement;
              if (input instanceof HTMLInputElement) input.setSelectionRange(DIGITS, DIGITS);
            })
          }
          pattern={REGEXP_ONLY_DIGITS}
          inputMode="tel"
          // Keep autofill and password-manager badges off the field; they draw icons over the blanks.
          autoComplete="off"
          pushPasswordManagerStrategy="none"
          data-1p-ignore
          data-lpignore="true"
          data-bwignore
          data-form-type="other"
          spellCheck={false}
          data-gramm="false"
          data-enable-grammarly="false"
          aria-label="Phone number"
          pasteTransformer={(pasted) => pasted.replace(/\D/g, "").slice(-DIGITS)}
          containerClassName="flex items-end gap-1"
          render={({ slots }) => (
            <>
              <Punct>(</Punct>
              {slots.slice(0, 3).map((s, i) => (
                <Slot key={i} {...s} />
              ))}
              <Punct>)</Punct>
              <span className="w-3" />
              {slots.slice(3, 6).map((s, i) => (
                <Slot key={i + 3} {...s} />
              ))}
              <Punct>-</Punct>
              {slots.slice(6).map((s, i) => (
                <Slot key={i + 6} {...s} />
              ))}
            </>
          )}
        />
        <div className="mb-1 h-11 w-11">
          <AnimatePresence>
            {complete && (
              <motion.button
                type="submit"
                aria-label="Continue"
                initial={{ scale: 0.4, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.4, opacity: 0, transition: { duration: 0.12 } }}
                transition={{ type: "spring", duration: 0.45, bounce: 0.4 }}
                className="flex h-11 w-11 items-center justify-center rounded-full bg-[#0a84ff] text-white"
              >
                <ArrowUp className="h-5 w-5" strokeWidth={2.75} />
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.form>
  );
}
