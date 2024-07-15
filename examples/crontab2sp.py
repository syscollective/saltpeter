#!/usr/bin/env python3

import sys
import argparse
import yaml
import re

def parse_crontab_line(line):
    parts = line.split()
    if len(parts) < 6:
        raise ValueError("Invalid crontab line format")
    
    schedule = parts[:5]
    user = parts[5]
    command = ' '.join(parts[6:])
    
    return {
        'min': schedule[0],
        'hour': schedule[1],
        'dom': schedule[2],
        'mon': schedule[3],
        'dow': schedule[4],
        'user': user,
        'command': command
    }

def generate_saltpeter_config(crontab_line, targets, target_type, number_of_targets, remove_output_redirection, command_prefix):
    parsed_line = parse_crontab_line(crontab_line)

    #remove output redirection from command
    pattern = r'\s*(?:[12]?>+&?\s*[/\w.-]+|\|&?\s*[/\w.-]+)'
    cleaned_command = re.sub(pattern, '', parsed_line['command']) 

    # Simplistic way to generate a name
    command_split = cleaned_command.split()
    if command_split[0] in ['python', 'python3', 'php', 'sh', 'bash', 'perl']:
        config_name = command_split[1].split('/')[-1]

    if remove_output_redirection:
        command = cleaned_command
    else:
        command = parsed_line['command']

    if command_prefix:
        command = f"{command_prefix} '{command}'"
    
    return {
        config_name: {
            'year': '*',
            'mon': parsed_line['mon'],
            'dow': parsed_line['dow'],
            'dom': parsed_line['dom'],
            'hour': parsed_line['hour'],
            'min': parsed_line['min'],
            'sec': '1',
            'cwd': '/',
            'user': parsed_line['user'],
            'command': command,
            'soft_timeout': 0,
            'hard_timeout': 0,
            'targets': targets,
            'target_type': target_type,
            'number_of_targets': number_of_targets
        }
    }

def main():
    parser = argparse.ArgumentParser(description="Convert crontab to Saltpeter YAML config")
    parser.add_argument('-f', '--file', help="File containing crontab config")
    parser.add_argument('--targets', required=True, help="Targets for the Saltpeter config")
    parser.add_argument('--target_type', required=True, help="Target type for the Saltpeter config")
    parser.add_argument('--number_of_targets', type=int, required=True, help="Number of targets for the Saltpeter config")
    parser.add_argument('--command_prefix', required=False, help="A prefix for the command")
    parser.add_argument('--remove_output_redirection', action="store_true", help="Remove output redirection from the command")
    
    args = parser.parse_args()
    
    if args.file:
        with open(args.file, 'r') as file:
            crontab_lines = file.readlines()
    else:
        crontab_lines = sys.stdin.readlines()
    
    configs = {}
    for line in crontab_lines:
        line = line.strip()
        if line and not line.startswith('#'):
            config = generate_saltpeter_config(line, args.targets, args.target_type, args.number_of_targets, args.remove_output_redirection, args.command_prefix)
            configs.update(config)
    
    print(yaml.dump(configs, default_flow_style=False))

if __name__ == "__main__":
    main()
