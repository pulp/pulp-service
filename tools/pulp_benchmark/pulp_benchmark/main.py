# pulp_benchmark/main.py
import importlib.util
from pathlib import Path
import logging
import click

from .cli import cli

def discover_and_register_plugins(cli_group: click.Group):
    """
    Dynamically discovers and registers commands from the 'plugins' directory.
    """
    plugins_dir = Path(__file__).parent / "plugins"
    
    # Iterate over every .py file in the plugins directory
    for filepath in plugins_dir.glob("*.py"):
        if filepath.name.startswith("__"):
            continue

        # Create a module name (e.g., 'pulp_benchmark.plugins.my_command')
        module_name = f"pulp_benchmark.plugins.{filepath.stem}"
        
        # Import the module from its file path
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Inspect the imported module for click.Command objects
            for attribute_name in dir(module):
                attribute = getattr(module, attribute_name)
                if isinstance(attribute, click.Command):
                    # Add the found command to the main CLI group
                    cli_group.add_command(attribute)
                    # This line will now work correctly
                    logging.info(f"Registered plugin command: '{attribute.name}'")

def main():
    """
    Application entry point. Discovers plugins, registers them, and runs the CLI.
    """
    discover_and_register_plugins(cli)
    
    # Run the fully populated CLI
    cli()

if __name__ == "__main__":
    main()
