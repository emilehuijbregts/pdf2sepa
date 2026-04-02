# Entry point voor de PDF2SEPA desktop applicatie; start de app en orkestreert basis-flow.


def main() -> None:
    from main_window import main as run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
